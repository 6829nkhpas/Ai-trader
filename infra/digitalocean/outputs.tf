output "droplet_ip" {
  description = "Public IPv4 of the droplet."
  value       = digitalocean_droplet.app.ipv4_address
}

output "droplet_id" {
  description = "Droplet ID."
  value       = digitalocean_droplet.app.id
}

output "ssh_command" {
  description = "SSH into the droplet."
  value       = "ssh -i <path-to-private-ssh-key> root@${digitalocean_droplet.app.ipv4_address}"
}

output "service_endpoints" {
  description = "Data-plane endpoints for desktop clients."
  value = {
    aggregator_ws     = "ws://${digitalocean_droplet.app.ipv4_address}:8080"
    ohlc_ws           = "ws://${digitalocean_droplet.app.ipv4_address}:8081"
    predictive_ws     = "ws://${digitalocean_droplet.app.ipv4_address}:8082"
    insights_ws       = "ws://${digitalocean_droplet.app.ipv4_address}:8083"
    ingestion_control = "${digitalocean_droplet.app.ipv4_address}:8085"
  }
}
