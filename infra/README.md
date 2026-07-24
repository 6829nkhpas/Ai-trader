# Infrastructure (OpenTofu → Oracle Cloud Always Free)

Provisions the cost-optimized beta host for the Ai-trader data plane:

| Resource | Notes |
|---|---|
| VCN + public subnet | `10.0.0.0/16` / `10.0.1.0/24` |
| Internet Gateway + route table | No NAT gateway / no load balancer → stays Always-Free |
| Security list | Opens SSH (22) + app ports 8080–8085, 8087. Datastore ports stay private. |
| Compute instance | `VM.Standard.A1.Flex`, 4 OCPU / 24 GB, Ubuntu 22.04 aarch64, 100 GB boot |
| cloud-init | Installs Docker + compose, opens instance firewall ports |

**Cost:** everything here fits inside the OCI Always Free allowances (A1 4 OCPU / 24 GB, 200 GB block storage, 1 free public IP, 10 TB/mo egress). Expected bill: **$0**.

## Local usage

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # fill in (already done for this repo — gitignored)
tofu init
tofu plan
tofu apply
# ... later ...
tofu destroy      # tears everything down
```

State is **local** by default (`terraform.tfstate`, gitignored). No bootstrap needed for a single operator.

## Remote state (for CI / multiple operators)

Uses OCI Object Storage via its S3-compatible API (Always Free, 20 GB):

1. Create a private bucket (e.g. `stratai-tfstate`).
2. Get your namespace: `oci os ns get`.
3. Create **Customer Secret Keys** (Console → Identity → your user → Customer Secret Keys). These are the S3 access/secret keys.
4. `cp backend.hcl.example backend.hcl`, fill in namespace/bucket, uncomment the `backend "s3" {}` block in `versions.tf`, then:
   ```bash
   AWS_ACCESS_KEY_ID=<key> AWS_SECRET_ACCESS_KEY=<secret> tofu init -backend-config=backend.hcl -reconfigure
   ```

## GitHub Actions

- `infra-plan.yml` — fmt/validate/plan on PRs touching `infra/**`.
- `infra-apply.yml` — apply on merge to `main`, gated by the **`production`** Environment (add required reviewers there so every apply needs approval).

Both generate the S3 backend block in CI and read credentials from repo secrets/vars.

### Repository **secrets** (Settings → Secrets and variables → Actions → Secrets)
| Secret | Value |
|---|---|
| `OCI_TENANCY_OCID` | tenancy OCID |
| `OCI_USER_OCID` | API user OCID |
| `OCI_FINGERPRINT` | API key fingerprint |
| `OCI_PRIVATE_KEY` | full PEM contents of the API private key |
| `OCI_SSH_PUBLIC_KEY` | contents of `keys/thestratai-public.pem` |
| `OCI_S3_ACCESS_KEY` | Customer Secret Key — access key |
| `OCI_S3_SECRET_KEY` | Customer Secret Key — secret |

### Repository **variables** (same page → Variables)
| Variable | Value |
|---|---|
| `OCI_REGION` | `ap-mumbai-1` |
| `TFSTATE_BUCKET` | `stratai-tfstate` |
| `TFSTATE_NAMESPACE` | your Object Storage namespace (`oci os ns get`) |

## After apply

`tofu output` prints the public IP and service endpoints. Then follow
`../DEPLOYMENT.md` from step 4 (configure `.env`, build, launch the compose stack).
cloud-init already installed Docker and opened the instance firewall.

## A1 capacity note

Free A1 shapes are frequently "Out of host capacity" in busy regions. If apply
fails with that error, change `availability_domain_index` (0 → 1 → 2) in
`terraform.tfvars` and retry, or retry later.

## Security

- `keys/`, `terraform.tfvars`, `backend.hcl`, and all state files are gitignored — never commit them.
- Datastore ports are not exposed publicly; reach QuestDB/Kafka via SSH tunnel.
- Narrow `ssh_ingress_cidr` / `app_ingress_cidr` to known IPs once your beta users are fixed.
