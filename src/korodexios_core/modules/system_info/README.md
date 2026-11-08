# system_info

Host diagnostics proving scoped configuration, events, and declarative CLI
registration. Built-in; discovered from `korodexios_core/modules/system_info/`.

## Contract

- Lifecycle: `init` reads the scoped config, `start` collects the first snapshot
  and publishes it, `stop` releases counters and ends the load watcher. With
  `watch_load` on, one daemon thread samples the load average; it is reported through
  `workers()` so supervision can watch it.
- Events published: `system.info.collected` (payload: the snapshot sections).
- CLI contributed: `kdx system info`, `kdx system diagnostics`, `kdx system disk`.
- Manifest: `kdx_module.toml` (`priority = 40`, `entrypoint = "MODULE"`).

## Configuration keys (`modules.system_info.*`)

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | gate registration at discovery |
| `disk_path` | `.` | filesystem reported by `kdx system disk` |
| `include_cpu_model` | `true` | include the CPU model string in snapshots |
| `watch_load` | `true` | sample the load average on a watched background thread |
| `load_interval` | `5.0` | seconds between load readings (never below the module's minimum) |
| `metrics` | `true` | publish host CPU, load, memory and disk as `kdx_host_*` metrics |

## Portability

Total memory resolves in order: `/proc/meminfo`, then POSIX `sysconf`
(`SC_PHYS_PAGES` x `SC_PAGE_SIZE`), then `unavailable`. Windows has no
`sysconf`, so `kdx system info` reports `-` there instead of guessing.

## How to test

`pytest -q tests/test_system_info.py` (collector boundaries and CLI handlers); the
live check is `kdx system info`.
