output "instance_ip" {
  description = "Public IPv4. Point the app.stratai.live A record here at cutover, and set the DEPLOY_HOST GitHub Actions variable to it."
  value       = utho_cloud_instance.app.ip
}

output "instance_id" {
  description = "Utho instance ID (for console / API calls)."
  value       = utho_cloud_instance.app.id
}

output "firewall_id" {
  description = "Firewall ID. Add the FIREWALL.md rules to THIS firewall — the provider cannot define them."
  value       = utho_firewall.fw.id
}

output "specs" {
  description = "Provisioned CPU / RAM / disk, so you can confirm the plan ID resolved to the 16 GB machine you intended."
  value = {
    cpu      = utho_cloud_instance.app.cpu
    ram      = utho_cloud_instance.app.ram
    disksize = utho_cloud_instance.app.disksize
  }
}

output "monthly_cost" {
  description = "Provider-reported cost. Divide your Utho credit balance by this to get real runway — verify it matches expectations before relying on it."
  value       = utho_cloud_instance.app.cost
}

output "next_steps" {
  description = "Post-apply checklist."
  value       = <<-EOT
    1. Add firewall rules (Terraform cannot): see infra/utho/FIREWALL.md
    2. Bootstrap Docker over SSH:
         scp -i keys/stratai_deploy infra/utho/bootstrap.sh root@${utho_cloud_instance.app.ip}:/root/
         ssh -i keys/stratai_deploy root@${utho_cloud_instance.app.ip} 'bash /root/bootstrap.sh'
    3. Migrate data + deploy: docs/DEPLOYMENT_UTHO.md sections 6-7
    4. Verify BEFORE cutover: docs/DEPLOYMENT_UTHO.md section 8
  EOT
}
