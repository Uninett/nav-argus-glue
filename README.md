# NAV <-> Argus glue service

This is a glue service for integration between
[Argus](https://github.com/Uninett/Argus), the alert aggregation server, and
[NAV](https://github.com/Uninett/nav) (Network Administration Visualized), the
network monitoring software suite provided by Uninett.

## How it works

`navargus` acts as a NAV event engine export script, accepting stacked,
JSON-serialized alert objects on its STDIN. When configured as the export
script in
[eventengine.conf](https://github.com/Uninett/nav/blob/0059f49ec36754fedcb385ecc50767729accbe7d/python/nav/etc/eventengine.conf#L2-L5),
the event engine will feed `navargus` a continuous stream of NAV alerts as they
are generated, and `navargus` will use these to either create new incidents in
the Argus API, or resolve existing ones as needed.


## Configuration

`navargus` is configured via `navargus.yml`. Since `navargus` is designed to
run in conjunction with NAV's event engine, this config file must be placed in
NAV's config directory (typically `/etc/nav`).

In the Argus admin UI, you need to create a new "Source system" for your NAV
installation. This will also automatically create an Argus user account for
your NAV installation. Now, use the Argus admin UI to also create an
authentication token for your user.

`navargus.yml` must at minimum contain the base URL of your Argus API server
and the API token you generated to be able to talk to the Argus API. An example:

```yml
---
api:
    url: https://argus.example.com/api/v1
    token: very-long-and-secret-string
```

You can now test whether `navargus` is able to read this configuration and
actually talk to the API using the command `navargus --test-api`.

See the `navargus.example.yml` file for more configuration examples.

## Code style

This module uses Black as a source code formatter for Python code.

A pre-commit hook will format new code automatically before committing.
To enable this pre-commit hook, run

```console
$ pre-commit install
```