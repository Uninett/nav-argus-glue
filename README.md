# NAV ↔ Argus glue service

This is a glue service for integration between
[Argus](https://github.com/Uninett/Argus), the alert aggregation server, and
[Network Administration Visualized](https://github.com/Uninett/nav) (NAV), the
network monitoring software suite provided by Uninett.

## How it works

`navargus` acts as an export script for the NAV event engine. It accepts
stacked, JSON-serialized alert objects on its STDIN.
The event engine will feed `navargus` a continuous stream of NAV alerts as they
are generated. `navargus` uses these alerts to either create new incidents in
the Argus API, or resolve existing ones as needed.

Refer to the Argus server documentation to learn more about [integrating monitoring 
systems](https://argus-server.readthedocs.io/en/latest/integrating-monitoring-systems.html)
with Argus.

## Configuration

NAV
---
Add `navargus` in the `[export]` section of the `eventengine.conf` file.

```ini
[export]
# If set, the script option will point to a program that will receive a
# continuous stream of JSON serialized alert objects on its STDIN.
script = /path/to/navargus
```

Argus
-----
In the Argus admin interface, create a new "Source system" for your
NAV instance. This will automatically create an Argus user account for
this instance. Now, use the Argus admin interface to create an authentication
token for your user.

NAV-Argus glue service
----------------------
`navargus` is configured via `navargus.yml`. Since `navargus` is designed to
run in conjunction with NAV's event engine, this config file must be placed in
NAV's config directory (typically `/etc/nav`).

A minimal `navargus.yml` contains the base URL of your Argus server and the
Argus API token generated above.

An example:

```yml
---
api:
    url: https://argus.example.com/api/v1
    token: very-long-and-secret-string
```

The configuration file may optionally contain a list of tags.
These tags will be added to all incidents created by this NAV instance.
To learn more about tags, refer to
[Argus documentation](https://argus-server.readthedocs.io/).

A full configuration file example is provided in
[navargus.example.yml](navargus.example.yml).

Now, you can run the command `navargus --test-api` to verify that the glue
service is properly configured and able to query the Argus API.

## Code style

This module uses Black as a source code formatter for Python code.

A pre-commit hook will format new code automatically before committing.
To enable this pre-commit hook, run

```console
$ pre-commit install
```
