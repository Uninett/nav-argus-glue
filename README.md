NAV ↔️ Argus glue service
==========================

This is a glue service for integration between
[Argus](https://github.com/Uninett/Argus), the alert aggregation server, and
[Network Administration Visualized](https://github.com/Uninett/nav) (NAV), the
network monitoring software suite provided by Uninett.

How it works
============

`navargus` acts as a NAV event engine export script. It accepts stacked,
JSON-serialized alert objects on its STDIN. When configured as the export
script in
[eventengine.conf](https://github.com/Uninett/nav/blob/0059f49ec36754fedcb385ecc50767729accbe7d/python/nav/etc/eventengine.conf#L2-L5),
the event engine will feed `navargus` a continuous stream of NAV alerts as they
are generated. Then `navargus` will use these alerts to either create new
incidents in the Argus API, or resolve existing ones as needed.

Refer to the Argus server documentation to learn more about [integrating monitoring 
systems](https://argus-server.readthedocs.io/en/latest/integrating-monitoring-systems.html)
with Argus.

Configuration
=============

`navargus` is configured via `navargus.yml`. Since `navargus` is designed to
run in conjunction with NAV's event engine, this config file must be placed in
NAV's config directory (typically `/etc/nav`).

In the Argus admin interface, create a new "Source system" for your
NAV instance. This will automatically create an Argus user account for
this specific instance. Now, use the Argus admin interface to create an
authentication token for your user.

`navargus.yml` must at minimum contain the base URL of your Argus API server
and the API token you generated to be able to talk to the Argus API. An example:

```yml
---
api:
    url: https://argus.example.com/api/v1
    token: very-long-and-secret-string
```

Additionally, configuration file holds a list of tags that `navargus` will add
to all incidents created by this NAV instance.

See the [navargus.example.yml](navargus.example.yml) file for a full
configuration example.

Use the command `navargus --test-api` to check whether `navargus` is able to read
this configuration and query the Argus API.
