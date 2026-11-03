#!/bin/bash
# Creates the KinKudos family server on Google Cloud's free-tier e2-micro.
# Run in Cloud Shell:
#   bash <(curl -fsSL https://raw.githubusercontent.com/onepersonstartup901/kinkudos-family/main/deploy/gcp/create.sh)
set -euo pipefail
REGION=us-west1
ZONE=us-west1-b
PROJECT="${PROJECT:-kinkudos-family-$(date +%y%m%d)-$RANDOM}"
BILLING="${BILLING:-$(gcloud billing accounts list --filter=open=true --format='value(name)' --limit=1)}"
BILLING="${BILLING#billingAccounts/}"
SCRIPT_URL=https://raw.githubusercontent.com/onepersonstartup901/kinkudos-family/main/deploy/gcp/startup.sh

echo "== Project $PROJECT on billing account $BILLING"
gcloud projects create "$PROJECT" --name="KinKudos family"
gcloud billing projects link "$PROJECT" --billing-account="$BILLING"
gcloud config set project "$PROJECT"
gcloud services enable compute.googleapis.com

echo "== Budget alert (5 a month, emails at 50% and 100%)"
gcloud services enable billingbudgets.googleapis.com \
  && gcloud billing budgets create --billing-account="$BILLING" \
    --display-name="KinKudos server" --budget-amount=5 \
    --filter-projects="projects/$PROJECT" \
    --threshold-rule=percent=0.5 --threshold-rule=percent=1.0 \
  || echo "(budget alert skipped; you can add one in Billing > Budgets)"

echo "== Server"
curl -fsSL "$SCRIPT_URL" -o /tmp/kinkudos-startup.sh
TOKEN=$(openssl rand -hex 20)
gcloud compute addresses create kinkudos-ip --region="$REGION"
gcloud compute firewall-rules create kinkudos-web --direction=INGRESS \
  --allow=tcp:80,tcp:443 --target-tags=kinkudos-web
gcloud compute instances create kinkudos --zone="$ZONE" --machine-type=e2-micro \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-standard \
  --address=kinkudos-ip --tags=kinkudos-web \
  --metadata-from-file=startup-script=/tmp/kinkudos-startup.sh \
  --metadata=kinkudos-setup-token="$TOKEN"

IP=$(gcloud compute addresses describe kinkudos-ip --region="$REGION" --format='value(address)')
HOST="${IP//./-}.sslip.io"
echo
echo "KinKudos address: https://$HOST/"
echo "Setup page:       https://$HOST/setup/"
echo "Setup code:       $TOKEN"
echo "The server needs about 5 minutes to install before these pages load."
