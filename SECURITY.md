# Security

ABMfact runs the model you point it at. An audit file can name any Python module or any program, so
treat audit files from others like code: read them before running them.

Results are written only to the output directory you choose. ABMfact makes no network requests.

The dependencies of every extra are checked with pip-audit against known vulnerabilities on each change
and once a week.

To report a vulnerability, please use GitHub's private vulnerability reporting
(Security tab, "Report a vulnerability") rather than a public issue. You should receive a reply within
seven days.
