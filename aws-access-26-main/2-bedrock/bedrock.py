#!/bin/bash
# Re-run this script any time your credentials expire or you open a new terminal.
# Usage: bash set_aws_creds.sh  (then open a new terminal, or run: source ~/.aws_workshop_env)

echo "Paste your AWS credential lines from Workshop Studio, then press Enter on a blank line:"

key_id=""
secret_key=""
session_token=""

while IFS= read -r line; do
    [[ -z "$line" ]] && break
    # Strip common prefixes
    line="${line#export }"
    line="${line#set }"
    # Split on first = to get key and value
    key="${line%%=*}"
    value="${line#*=}"
    # Strip surrounding quotes
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"

    case "$key" in
        AWS_ACCESS_KEY_ID)     key_id="$value" ;;
        AWS_SECRET_ACCESS_KEY) secret_key="$value" ;;
        AWS_SESSION_TOKEN)     session_token="$value" ;;
    esac
done

region="us-east-1"

if [[ -z "$key_id" || -z "$secret_key" || -z "$session_token" ]]; then
    echo -e "\nERROR: Could not find all three credentials in what you pasted."
    echo "Expected lines containing: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN"
    exit 1
fi

# Apply to the current terminal session
export AWS_ACCESS_KEY_ID="$key_id"
export AWS_SECRET_ACCESS_KEY="$secret_key"
export AWS_SESSION_TOKEN="$session_token"
export AWS_DEFAULT_REGION="$region"

# Persist for all future terminal windows by writing to a dedicated file
AWS_ENV_FILE="$HOME/.aws_workshop_env"
cat > "$AWS_ENV_FILE" << EOF
export AWS_ACCESS_KEY_ID="$key_id"
export AWS_SECRET_ACCESS_KEY="$secret_key"
export AWS_SESSION_TOKEN="$session_token"
export AWS_DEFAULT_REGION="$region"
EOF

# Add a one-time source line to your shell profile so new terminals load it automatically
if [[ "$SHELL" == */zsh ]]; then
    SHELL_RC="$HOME/.zshrc"
else
    SHELL_RC="$HOME/.bashrc"
fi
if ! grep -q "aws_workshop_env" "$SHELL_RC" 2>/dev/null; then
    echo "" >> "$SHELL_RC"
    echo "source $HOME/.aws_workshop_env" >> "$SHELL_RC"
    echo "Added auto-load line to $SHELL_RC"
fi

echo -e "\nCredentials saved. Verifying with AWS..."
aws sts get-caller-identity
echo -e "\nOpen a new terminal window (or run: source ~/.aws_workshop_env) to use these credentials there too."
