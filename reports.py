"""AI incident reports via Bedrock Runtime converse.

Facts (what / where / when / OSHA rule / fine) come from our own data, never
from the model, so the LLM cannot invent a rule or a dollar amount. Bedrock
writes only the narrative: summary, risk, fix, prevention.

Reports are generated in a background thread so /analyze stays fast. If Bedrock
fails (or MOCK_MODE is on), a template narrative is stored instead, so every
violation always has a report.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor

from botocore.exceptions import BotoCoreError, ClientError

import config
import records
import store
from tracker import utc_timestamp

_pool = ThreadPoolExecutor(max_workers=2)

SYSTEM_PROMPT = """You write concise workplace-safety incident reports for SafeSight, \
a camera that checks whether workers wear required protective equipment.

You are given the verified facts of one violation as JSON. Write the narrative only.

Rules:
- Use only the facts given. Do not invent people, injuries, causes, or equipment.
- Money is always "potential fine exposure". Never say the company will be fined or was fined.
- Do not restate or change the fine amount or OSHA rule; they are shown separately.
- Plain language a site supervisor can act on. No markdown.

Return ONLY a JSON object with these string keys:
  "summary":    1-2 sentences describing what happened.
  "risk":       1-2 sentences on the injury or health risk this gear prevents.
  "fix":        the immediate corrective action, 1-2 sentences.
  "prevention": 1-2 sentences on preventing recurrence (training, signage, gear stations)."""

# Template narrative: used in MOCK_MODE and whenever Bedrock is unavailable.
TEMPLATES = {
    "HAND_COVER": {
        "risk": "Bare hands are exposed to chemical burns, cuts, and absorption of hazardous substances through the skin.",
        "fix": "Stop the task and put on gloves suited to the hazard (check the SDS or job hazard assessment) before resuming.",
        "prevention": "Stock gloves at the zone entrance and include glove selection in the next toolbox talk.",
    },
    "HEAD_COVER": {
        "risk": "An unprotected head is exposed to falling objects and impact with fixed structures, which can cause serious injury.",
        "fix": "Put on head protection appropriate to the hazard before continuing work in the zone.",
        "prevention": "Post head-protection signage at entry points and keep spare hard hats at the zone entrance.",
    },
    "FACE_COVER": {
        "risk": "Without a face covering, the worker may inhale airborne contaminants such as dust, vapors, or particulates.",
        "fix": "Put on the required face covering or respirator; respirators must be used under the site's respiratory protection program, including fit testing.",
        "prevention": "Confirm workers in this zone are enrolled in the respiratory protection program and post the requirement at the entrance.",
    },
}


def _facts(item):
    rule = config.rule_for(None, item["gear_type"])  # fallback if the record predates per-zone rules
    items = records.items_of(item)
    f = {
        "zone": item["zone_name"],
        "missing_gear": " and ".join(records.labels(item)),
        "detected_by": " + ".join(dict.fromkeys(i.get("detector") or config.detector(i["gear"]) for i in items)),
        "people_without_gear": int(item.get("people", 1)),
        "time_utc": item["timestamp"],
        "osha_rule": "; ".join(dict.fromkeys(i.get("rule") or "" for i in items)) or item.get("osha_rule") or rule["label"],
        "severity": item.get("severity") or rule["severity"],
        "potential_fine_exposure_usd": sum(int(i.get("fine", 0)) for i in items),
        "items": [{"item": i.get("label"), "rule": i.get("rule"), "fine_usd": int(i.get("fine", 0)),
                   "detected_by": i.get("detector")} for i in items],
        "missing_continuously_seconds_before_logging": config.VOICE_AFTER_SECONDS,
    }
    reasons = [i.get("ai_reason") for i in items if config.is_ai(i["gear"]) and i.get("ai_reason")]
    if reasons:
        f["ai_vision_reason"] = "; ".join(reasons)
        f["confirmed_by_consecutive_ai_checks"] = config.AI_DEBOUNCE_CHECKS
    if all(config.is_ai(i["gear"]) for i in items):
        f.pop("missing_continuously_seconds_before_logging")
    return f


def _ai_template(rule):
    return {
        "risk": f'Breaking the "{rule}" rule exposes workers to the hazard the site policy was written to control.',
        "fix": f'Stop and correct the situation so the "{rule}" rule is followed before work continues.',
        "prevention": f'Re-brief the team on the "{rule}" rule and post it at the zone entrance.',
    }


def template_narrative(item):
    f = _facts(item)
    items = records.items_of(item)
    first = items[0]["gear"]
    t = TEMPLATES.get(first) or _ai_template(f["missing_gear"])
    people = f["people_without_gear"]
    who = "1 worker was" if people == 1 else f"{people} workers were"
    if all(config.is_ai(i["gear"]) for i in items):
        reason = f.get("ai_vision_reason")
        summary = (f'AI vision flagged the "{f["missing_gear"]}" rule in {f["zone"]} on '
                   f"{config.AI_DEBOUNCE_CHECKS} consecutive checks" + (f": {reason}" if reason else "."))
    else:
        summary = (f"{who} detected in {f['zone']} without required {f['missing_gear']}, "
                   f"missing continuously for at least {config.VOICE_AFTER_SECONDS} seconds.")
    if len(items) > 1:  # combine the fixes for every item
        extra = [TEMPLATES.get(i["gear"]) or _ai_template(i.get("label", "")) for i in items[1:]]
        t = {k: " ".join([t[k]] + [e[k] for e in extra]) for k in ("risk", "fix", "prevention")}
    return {"summary": summary, **t}


def _parse_json(text):
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start:end + 1])
    return {k: str(data.get(k, "")).strip() for k in ("summary", "risk", "fix", "prevention")}


def bedrock_narrative(item):
    from aws_clients import bedrock

    resp = bedrock.converse(
        modelId=config.BEDROCK_MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": json.dumps(_facts(item))}]}],
        # No temperature: current Claude models reject sampling params.
        inferenceConfig={"maxTokens": 4000},
    )
    # Keep only text blocks (thinking/reasoning blocks may also be present).
    text = "".join(b["text"] for b in resp["output"]["message"]["content"] if "text" in b)
    return _parse_json(text)


def build_report(item):
    if config.MOCK_MODE:
        narrative, source = template_narrative(item), "template (mock mode)"
    elif not config.BEDROCK_MODEL_ID:
        narrative, source = template_narrative(item), "template (BEDROCK_MODEL_ID not set)"
    else:
        try:
            narrative, source = bedrock_narrative(item), f"Amazon Bedrock ({config.BEDROCK_MODEL_ID})"
        except (ClientError, BotoCoreError, ValueError, KeyError) as e:
            print(f"[reports] Bedrock failed, using template: {e}")
            narrative, source = template_narrative(item), "template (Bedrock unavailable)"
    return {**narrative, "generated_by": source, "generated_at": utc_timestamp(time.time())}


def _generate_and_save(item):
    try:
        report = build_report(item)
        store.set_report(item["zone_id"], item["timestamp"], json.dumps(report), "READY")
    except Exception as e:  # background thread: never crash silently
        print(f"[reports] could not save report for {item['zone_id']} {item['timestamp']}: {e}")
        try:
            store.set_report(item["zone_id"], item["timestamp"], "", "FAILED")
        except Exception:
            pass


def submit(item):
    """Generate the report in the background."""
    _pool.submit(_generate_and_save, item)


# ---------------- Weekly report ----------------
WEEKLY_SYSTEM_PROMPT = """You write a short weekly safety briefing for a site manager, based on \
statistics from SafeSight, a PPE compliance camera (it detects face covers, gloves, and head protection).

You are given verified statistics as JSON. Use only those numbers; do not invent any.
Money is always "potential fine exposure" or "potential fines avoided"; never say the company was or will be fined.
If the data is flagged as including sample data, do not mention it (the UI shows that separately).
Plain language, no markdown.

Return ONLY a JSON object:
  "summary": 2-3 sentences: overall compliance picture this week.
  "highlights": array of 2-3 short strings: notable patterns (riskiest zone, most-missed gear, fix speed).
  "recommendations": array of exactly 3 short, specific actions for next week."""


def _weekly_facts(st):
    w = st["week"]
    return {
        "violations_last_7_days": w["violations"],
        "resolved_within_5_min": w["resolved"],
        "potential_fine_exposure_open_usd": w["fine_exposure"],
        "potential_fines_avoided_usd": w["fines_avoided"],
        "average_seconds_to_fix": w["avg_seconds_to_fix"],
        "violations_by_gear": {config.gear_label(g): n for g, n in w["by_gear"].items()},
        "violations_by_zone": {z: sum(c.values()) for z, c in st["by_zone"].items()},
        "riskiest_zone": st["riskiest_zone"],
    }


def weekly_template(st):
    w, rz = st["week"], st["riskiest_zone"]
    if not w["violations"]:
        return {"summary": "No PPE violations were recorded in the last 7 days.",
                "highlights": [], "recommendations": [
                    "Keep cameras running in all zones.",
                    "Spot-check that zone gear requirements are still correct.",
                    "Recognise teams for full compliance."]}
    top_gear = max(w["by_gear"], key=w["by_gear"].get)
    rate = round(100 * w["resolved"] / w["violations"])
    return {
        "summary": f"{w['violations']} PPE violations were recorded in the last 7 days; {rate}% were fixed within "
                   f"5 minutes, avoiding ${w['fines_avoided']:,} in potential fines. "
                   f"${w['fine_exposure']:,} in potential fine exposure remains from unresolved violations.",
        "highlights": [
            f"{rz['zone']} was the riskiest zone with {rz['violations']} violations.",
            f"Most frequent issue: {config.gear_label(top_gear)}.",
        ] + ([f"Average time to fix: {w['avg_seconds_to_fix']} seconds."] if w["avg_seconds_to_fix"] else []),
        "recommendations": [
            f"Run a short PPE refresher for everyone working in {rz['zone']}.",
            (f'Re-brief every shift on the "{config.gear_label(top_gear)}" rule and post it at zone entrances.'
             if config.is_ai(top_gear) else
             f"Place a {config.gear_label(top_gear)} station at the entrance of each zone that requires it."),
            "Ask supervisors to review open incidents daily and confirm the fix.",
        ],
    }


def weekly_report(st):
    narrative, source = None, None
    if not config.MOCK_MODE and config.BEDROCK_MODEL_ID and st["week"]["violations"]:
        try:
            from aws_clients import bedrock

            resp = bedrock.converse(
                modelId=config.BEDROCK_MODEL_ID,
                system=[{"text": WEEKLY_SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": json.dumps(_weekly_facts(st))}]}],
                inferenceConfig={"maxTokens": 4000},
            )
            text = "".join(b["text"] for b in resp["output"]["message"]["content"] if "text" in b)
            data = json.loads(text[text.find("{"): text.rfind("}") + 1])
            narrative = {
                "summary": str(data.get("summary", "")).strip(),
                "highlights": [str(x) for x in data.get("highlights", [])][:3],
                "recommendations": [str(x) for x in data.get("recommendations", [])][:3],
            }
            source = f"Amazon Bedrock ({config.BEDROCK_MODEL_ID})"
        except (ClientError, BotoCoreError, ValueError, KeyError, AttributeError) as e:
            print(f"[reports] weekly Bedrock call failed, using template: {e}")
            source = "template (Bedrock unavailable)"
    if narrative is None:
        narrative = weekly_template(st)
        source = source or ("template (mock mode)" if config.MOCK_MODE else "template")
    return {**narrative, "stats": st, "generated_by": source, "generated_at": utc_timestamp(time.time())}
