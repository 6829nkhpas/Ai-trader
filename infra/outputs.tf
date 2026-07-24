output "instance_public_ip" {
  description = "Public IP of the beta host."
  value       = oci_core_instance.app.public_ip
}

output "instance_id" {
  description = "OCID of the compute instance."
  value       = oci_core_instance.app.id
}

output "availability_domain" {
  description = "AD the instance landed in."
  value       = oci_core_instance.app.availability_domain
}

output "ssh_command" {
  description = "SSH into the host (default Ubuntu user)."
  value       = "ssh -i <path-to-private-ssh-key> ubuntu@${oci_core_instance.app.public_ip}"
}

output "service_endpoints" {
  description = "Data-plane endpoints for desktop clients."
  value = {
    aggregator_ws     = "ws://${oci_core_instance.app.public_ip}:8080"
    ohlc_ws           = "ws://${oci_core_instance.app.public_ip}:8081"
    predictive_ws     = "ws://${oci_core_instance.app.public_ip}:8082"
    insights_ws       = "ws://${oci_core_instance.app.public_ip}:8083"
    ingestion_control = "${oci_core_instance.app.public_ip}:8085"
  }
}
