# La Marzocco BLE Setup Guide

The espresso-bridge needs three credentials to communicate with your La Marzocco Linea Micra over Bluetooth:

| Credential | Where to find |
|---|---|
| `serial_number` | Printed on the machine, or in the La Marzocco Home app |
| `username` | Your La Marzocco Home app login email |
| `communication_key` | Extracted once using the method below |

## Getting the Communication Key

The `communication_key` (also called the BLE token) is used to authenticate Bluetooth commands. It's stored in La Marzocco's cloud and linked to your account.

### Method 1: Via the La Marzocco Cloud API (Recommended)

Use this Python snippet to fetch the key:

```python
import asyncio
from lmcloud import LaMarzoccoCloudClient

async def get_key():
    client = LaMarzoccoCloudClient()
    # This will prompt for La Marzocco app credentials
    await client.login("your_email@example.com", "your_password")

    # List your machines
    machines = await client.list_machines()
    for m in machines:
        print(f"Serial: {m['serial_number']}")
        print(f"Communication Key: {m['communication_key']}")

asyncio.run(get_key())
```

### Method 2: Via mitmproxy (If Method 1 doesn't work)

1. Install [mitmproxy](https://mitmproxy.org/)
2. Configure your phone to proxy through mitmproxy
3. Open the La Marzocco Home app and connect to your machine
4. Look for requests to `cms.lamarzocco.io/api/customer`
5. The response JSON contains `communicationKey` for each machine

### Method 3: QR Code (If accessible)

Some machines have a QR code inside the chassis that contains the BLE token. Check your machine's documentation.

## Configuration

Once you have all three values, add them to `config.yaml`:

```yaml
lamarzocco:
  serial_number: "LM12345"
  username: "your_email@example.com"
  communication_key: "your_communication_key_here"
```

## Verify Connection

```bash
# Scan for your machine
espresso-bridge lm scan

# Check status (requires config.yaml)
espresso-bridge lm status
```

## Troubleshooting

- **"No La Marzocco machines found"**: Make sure the machine is powered on and Bluetooth is enabled. The Pi must be within BLE range (~10m).
- **"Failed to connect"**: Verify your credentials. The communication key may need to be re-extracted if you change your La Marzocco account password.
- **Connection drops**: The lmcloud library auto-disconnects after 30 seconds of inactivity. The espresso-bridge reconnects automatically.
