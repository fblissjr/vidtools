"""Give a freshly installed Quick Action an enable/disable record.

A bundle can be complete, lint-clean, and present in the services database and
*still* never appear — not in Finder's Quick Actions menu, and not in System
Settings -> Login Items & Extensions -> Finder, so there is nowhere to switch it
on. That was the sixth distinct layer this feature has failed in, and the only
observable difference between the action that worked and the one that did not
was a missing entry under `NSServicesStatus` in the `pbs` preference domain.

The entry is what the System Settings toggle writes. Writing it here is the same
operation the GUI performs, done at install time so a first install behaves like
a second one.

Conservative on purpose:

* Only ever *adds*. An existing entry means the user has already expressed a
  preference — possibly "off" — and an installer must not overturn that.
* Goes through `defaults export` / `defaults import` rather than editing the
  plist on disk. `cfprefsd` caches these domains and will happily overwrite a
  file written behind its back.

Stdlib only, like `make_workflow.py`, so it cannot fail for a reason belonging
to the package it installs.
"""

import plistlib
import subprocess
import sys

# Observed format, read off the entries macOS itself wrote for the actions that
# were already working. The leading "(null)" is macOS's own placeholder, not a
# bug here.
KEY_TEMPLATE = "(null) - {name} - runWorkflowAsService"

# What macOS records for an enabled Finder Quick Action.
ENABLED = {
    "presentation_modes": {
        "ContextMenu": True,
        "FinderPreview": True,
        "ServicesMenu": True,
        "TouchBar": False,
    }
}


def _export(domain: str) -> dict:
    """Read the whole domain, refusing a read that clearly did not work.

    `defaults export` emits a complete `<dict/>` plist even for a domain that
    has never existed, so empty output means the read failed rather than "no
    entries yet". Treating it as an empty domain would be destructive: the
    write below replaces `NSServicesStatus` wholesale (`defaults import` merges
    at the top level but *replaces* nested keys), so writing a status dict
    rebuilt from nothing erases every other Quick Action's toggle.
    """
    exported = subprocess.run(["defaults", "export", domain, "-"],
                              capture_output=True, check=True).stdout
    if not exported.strip():
        raise SystemExit(
            f"error: could not read the '{domain}' preference domain. Refusing "
            f"to write, since doing so would drop every other service's "
            f"enable/disable record.")
    return plistlib.loads(exported)


def ensure_enabled(name: str, domain: str = "pbs") -> bool:
    """Add an enabled record for `name` unless one already exists.

    Returns True if an entry was written, False if one was already there.
    Raises SystemExit if the write did not stick.
    """
    key = KEY_TEMPLATE.format(name=name)

    prefs = _export(domain)
    status = prefs.setdefault("NSServicesStatus", {})
    if key in status:
        return False

    status[key] = {"presentation_modes": dict(ENABLED["presentation_modes"])}
    subprocess.run(["defaults", "import", domain, "-"],
                   input=plistlib.dumps(prefs), check=True)

    # Read back rather than trusting the exit code. `defaults import` returns 0
    # whether or not it wrote anything -- verified against an unwritable
    # directory -- so without this the installer prints "enabled:" over a write
    # that never landed, leaving the user in the exact invisible-action state
    # this module exists to prevent, told that it was fixed.
    if key not in _export(domain).get("NSServicesStatus", {}):
        raise SystemExit(
            f"error: wrote the enable record for {name!r} but it did not stick. "
            f"The '{domain}' domain may be managed or read-only.")
    return True


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: enable_service.py <service name> [defaults domain]")
    name = sys.argv[1]
    domain = sys.argv[2] if len(sys.argv) > 2 else "pbs"
    if ensure_enabled(name, domain):
        print(f"enabled: {name}")


if __name__ == "__main__":
    main()
