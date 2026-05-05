# Baseline Validation Report — vm1-samurai

**Generated at (UTC):** 2026-05-05T05:44:46Z
**Subscription:** `8a46e231-771b-40d7-90f5-6a8e17682197`
**Resource group:** `Samurai`
**Flow:** SamurAI Shield :: Legacy-to-Immutable Rebuild

---

## VM identity

| Attribute       | Value |
| --------------- | ----- |
| VM name         | `vm1-samurai` |
| VM ID           | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/Samurai/providers/Microsoft.Compute/virtualMachines/vm1-samurai` |
| Location        | `eastus` |
| Size            | `Standard_B1ms` |
| OS type         | `Linux` |
| Power state     | `VM running` |
| Availability zone | `1` |

## OS disk

| Attribute       | Value |
| --------------- | ----- |
| Name            | `vm1-samurai_OsDisk_1_2d9c0ae602e947aebbeb03c4d4eb1d47` |
| ID              | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/SAMURAI/providers/Microsoft.Compute/disks/vm1-samurai_OsDisk_1_2d9c0ae602e947aebbeb03c4d4eb1d47` |
| Caching         | `ReadWrite` |
| SKU             | `Standard_LRS` |
| Size (GiB)      | `0` |

## Data disks (0)

_None._

## Network

| Attribute       | Value |
| --------------- | ----- |
| NIC name        | `vm1-samurai814_z1` |
| NIC ID          | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/Samurai/providers/Microsoft.Network/networkInterfaces/vm1-samurai814_z1` |
| Private IP      | `10.1.0.4` |
| Subnet ID       | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/Samurai/providers/Microsoft.Network/virtualNetworks/vm1-samurai-vnet/subnets/default` |
| NSG ID          | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/Samurai/providers/Microsoft.Network/networkSecurityGroups/vm1-samurai-nsg` |
| Public IP       | `172.206.227.150` |
| Public IP alloc | `Static` |
| Public IP SKU   | `Standard` |

## Tags

_None._

## VM extensions

| Name | Publisher | Type | Version |
| ---- | --------- | ---- | ------- |
| `enablevmAccess` | `Microsoft.OSTCExtensions` | `VMAccessForLinux` | `1.5` |

## OS-level summary

_OS-level facts were not collected (target host unreachable or skipped)._

---

> This report is the input for steps 2 → 7 of the Legacy-to-Immutable
> Rebuild flow. It is committed to Git by step 7.
