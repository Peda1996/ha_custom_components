# ETA custom component for Home Assistant

Uses the ETA REST API to discover and poll values from an ETAtouch controller.

## Setup

Activate the ETA REST API on the heater first, then check this URL in a browser:

```text
http://<YOUR-ETA-IP>:8080/user/menu
```

Copy the `eta/` folder to `/config/custom_components/eta/` and restart Home Assistant.
Then add **ETA Heating** from Settings > Devices & services.

The folder includes local brand assets under `eta/brand/` for Home Assistant versions
that support custom integration logos.

YAML import is also supported:

```yaml
eta:
  host: 192.168.2.4
  port: 8080
  name: ETA Heating
  prefix: eta
  scan_interval: 60
```

Legacy platform setup is still supported with the fixed domain name:

```yaml
sensor:
  - platform: eta
    host: 192.168.2.4
    port: 8080
    prefix: eta

switch:
  - platform: eta
    host: 192.168.2.4
    port: 8080
    prefix: eta
```

## Discovery and polling

The integration reads `/user/menu`, reads current values through `/user/var<uri>`,
and stores discovered entities in Home Assistant storage. Later restarts reuse that
cache instead of probing all `/user/varinfo<uri>` endpoints again.

Writable two-state ETA values are exposed as switches. Switch writes use the documented
`POST /user/var<uri>` endpoint with form field `value`.

Entity IDs and unique IDs intentionally follow the old integration format. With the
default `prefix: eta`, entities are created as `sensor.eta_...` and
`switch.eta_..._schalter`, even when the integration title is `ETA Heating`.

The integration also migrates early `eta_heating_...` entity IDs back to `eta_...`
when the target entity ID is still free.

Options:

- `cache_discovery`: keep discovered entities in HA storage. Default: `true`.
- `use_variable_set`: poll through ETA `/user/vars/<name>` after setup. Default: `true`.
- `discovery_workers`: concurrent ETA requests during discovery. Default: `32`.
- `full_switch_discovery`: probe every text value for switch metadata. Default: `false`.

ETA exposes a large number of values. Common heating dashboard entities and all
detected switches are enabled by default; the remaining low-level values are disabled
by default and can be enabled manually in Home Assistant.
