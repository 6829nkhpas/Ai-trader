terraform {
  required_version = ">= 1.6.0"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = ">= 2.40.0"
    }
  }

  # Remote state optional. DigitalOcean Spaces is S3-compatible and works as a
  # backend the same way (see README). Local state by default.
  # backend "s3" {}
}
