#!/bin/bash

# Usage: ./copy-iam-role.sh <source-role-name> <new-role-name>

SOURCE_ROLE=$1
NEW_ROLE=$2

if [ -z "$SOURCE_ROLE" ] || [ -z "$NEW_ROLE" ]; then
    echo "Usage: $0 <source-role-name> <new-role-name>"
    exit 1
fi

# Get role details
aws iam get-role --role-name $SOURCE_ROLE --query 'Role.AssumeRolePolicyDocument' > assume-role-policy.json

# Get inline policies
aws iam list-role-policies --role-name $SOURCE_ROLE --query 'PolicyNames' --output text > inline-policies.txt

# Create new role
aws iam create-role --role-name $NEW_ROLE --assume-role-policy-document file://assume-role-policy.json


# Copy inline policies
while read -r policy_name; do
    policy_name=$(echo "$policy_name" | xargs)
    if [ -n "$policy_name" ]; then
        echo "Processing policy: $policy_name"
        aws iam get-role-policy --role-name $SOURCE_ROLE --policy-name $policy_name --query 'PolicyDocument' > inline-policy.json
        echo "Policy document content:"
        cat inline-policy.json
        if [ -s inline-policy.json ]; then
            aws iam put-role-policy --role-name $NEW_ROLE --policy-name $policy_name --policy-document file://inline-policy.json
        else
            echo "Policy document is empty for $policy_name"
        fi
    fi
done < inline-policies.txt

# Cleanup temp files
rm -f assume-role-policy.json attached-policies.txt inline-policies.txt inline-policy.json

echo "Role $SOURCE_ROLE copied to $NEW_ROLE successfully"