# Translating the chassis (and your project)

The chassis speaks English. A language is a gettext catalogue next to the code; the
chassis reads `.po` files directly, so there is no compile step and no extra tool.

## What is translated

Only what a person reads: help text, status messages, table and key labels. These stay
English in every language, so scripts, docs and support look the same everywhere:

- command and flag names (`kdx status`, `--json`, `locale set`);
- `--json` keys and values, logs, stderr diagnostics, exit codes;
- the `[✓] [?] [!] [i]` tags and configuration keys.

## Add a language (human or agent)

```bash
kdx locale extract            # refresh the template (src/korodexios_core/locales/korodexios.pot)
kdx locale add de             # create locales/de/korodexios.po from the template
kdx locale todo de --json     # what is left, with rules and the source location of each string
# translate every "msgid" into a JSON object {msgid: translation}, then:
kdx locale apply de answer.json        # validated; --dry-run checks without writing
kdx locale check                       # exit 3 until every catalogue is complete
kdx --locale de doctor                 # look at it
```

`apply` rejects an entry that is not in the template, is empty, or does not keep the
same `{placeholders}` as the source. It is safe to call repeatedly and to translate in
batches (`kdx locale todo de --limit 50 --json`).

## Rules for a translator

1. Keep every `{placeholder}` exactly as written (`{count}`, `{path}`), in any order.
2. Do not translate commands, flags, paths, config keys, URLs, or anything in quotes
   that the user would type: `kdx jobs show {job_id} --follow` keeps `kdx jobs show`
   and `--follow`; only the words around it change.
3. Keep the tone short and technical, like the English. No exclamation marks.
4. Option metavars in help (`SECONDS`, `PATTERN`, `KEY`) stay uppercase; you may translate
   them when the translation is also uppercase (`SEKUNDÁCH`).
5. Plural forms are written so one sentence works for any count (`file(s)`); translate
   it the same way.
6. Use the glossary below so a term means one thing across the catalogue.

## Glossary (Slovak shown; mirror the idea in your language)

| English | Slovak | Meaning |
| --- | --- | --- |
| chassis | šasi | the core that every Korodexios project is built on |
| daemon | démon | the long-running background process |
| control plane | riadiaca rovina | the HTTP endpoint the daemon serves |
| module | modul | a plug-in discovered by the chassis |
| job | úloha | a command running in the background on the daemon |
| token | token | the secret that identifies a caller |
| catalogue | katalóg | the file of translations for one language |
| drift | odchýlka | a file whose hash no longer matches its contract |

## For a module or a project

A domain is a name plus a directory:

```python
context.i18n.register("my-project", Path(__file__).parent / "locales")
message = context.translate("{count} files copied", domain="my-project", count=3)
```

Layout: `locales/my-project.pot` (template) and `locales/<locale>/my-project.po`. The
same `kdx locale ... --domain my-project` commands work on it; point
`kdx locale extract --domain my-project --source src/my_project` at your code.

To make a sentence translatable, write it once with named fields and let the values
stay out of the catalogue:

```python
write_line(tr("{count} files copied to {target}", count=n, target=path))
```

Fixed labels given to `print_section`, `print_key_values`, `print_table(headers=...)`
and static `write_line("...")` messages are found by `kdx locale extract` without any
wrapper. Never put values, JSON, or stderr text through `tr()`.

## Choosing the language

`core.locale` follows the usual precedence: `--locale sk`, then `KDX_CORE__LOCALE`, then
`kdx.local.yml` (`kdx locale set sk` writes it), then `kdx.yml`, then the default `en`.
`auto` reads `LC_ALL`, `LC_MESSAGES`, `LANG`. A regional code falls back to its language
(`sk_SK` uses `sk` when there is no `sk_SK` catalogue), then to English.
