# CHANGELOG

<!-- version list -->

## v0.32.0 (2026-11-13)

### Bug Fixes

- **dronesense**: Only real numbers reach CoT attributes; drop the relay URL from remarks
  ([`c386291`](https://github.com/pounde/FastTAK/commit/c386291ff30b1363b489c8416688e81b0877ce9c))

- **mediamtx**: Final-review fixes for auth docs, video-cot.sh and tests
  ([`394325a`](https://github.com/pounde/FastTAK/commit/394325a8d5585b195877ce93d8a2675e6d1debd2))

### Documentation

- **mediamtx**: DD-056, DroneSense re-import, publish control, rotation
  ([#143](https://github.com/pounde/FastTAK/pull/143),
  [`06b5f5c`](https://github.com/pounde/FastTAK/commit/06b5f5c5637c51d3fb439bc624eb06d5732d5706))

### Features

- **dronesense**: Viewer credential in CoT video, authenticated reconcile
  ([#143](https://github.com/pounde/FastTAK/pull/143),
  [`bfe0c5a`](https://github.com/pounde/FastTAK/commit/bfe0c5ab719f0d6520c1ad2c03b9334cbc9af6b1))

- **mediamtx**: Generate and require viewer, publisher and API passwords
  ([#143](https://github.com/pounde/FastTAK/pull/143),
  [`f04ac57`](https://github.com/pounde/FastTAK/commit/f04ac57e0106ef120462358eded21c932cc43757))

- **mediamtx**: No anonymous access; Digest; publishing off by default
  ([#143](https://github.com/pounde/FastTAK/pull/143),
  [`018f658`](https://github.com/pounde/FastTAK/commit/018f658e8e0f3c77b407ef8ae5cbbf65424a225d))

- **video-cot**: Publish with the publisher credential, send viewer URL
  ([#143](https://github.com/pounde/FastTAK/pull/143),
  [`fef6d65`](https://github.com/pounde/FastTAK/commit/fef6d65eedeb358d259b40cb3e7bedbfaff742ba))

### Testing

- **mediamtx**: End-to-end auth matrix, publish default, rotation
  ([#143](https://github.com/pounde/FastTAK/pull/143),
  [`0114964`](https://github.com/pounde/FastTAK/commit/011496415989b90edaa5f927b664c36581e60f0c))


## v0.31.1 (2026-11-13)

### Bug Fixes

- **groups**: A missing group is a 404, not a server error
  ([`539a4a8`](https://github.com/pounde/FastTAK/commit/539a4a8d1eb67c73efd1654cc13b3a5e366fbae6))

- **setup**: Reuse TAK Server images that already exist; --rebuild to force
  ([`dcd26cf`](https://github.com/pounde/FastTAK/commit/dcd26cfe51d18dd8fc09191b83f1b477902839ba))

### Chores

- Sync uv.lock with the v0.31.0 release
  ([`336d5e9`](https://github.com/pounde/FastTAK/commit/336d5e95143c9b308c41d7e2ba2e68d919f24e52))

- **mediamtx**: Pin 1.21.1 and refuse older pins
  ([#145](https://github.com/pounde/FastTAK/pull/145),
  [`5522209`](https://github.com/pounde/FastTAK/commit/5522209dc60b73fa1fc5479506f51676212e4104))

### Documentation

- Keep an export of the TAK Server images; what to do when their build fails
  ([`df316ce`](https://github.com/pounde/FastTAK/commit/df316ce3d13cf57c5778aafe5c1c17c25bfac7fa))


## v0.31.0 (2026-09-22)

### Bug Fixes

- **alerts**: An SMS provider error returns False instead of raising out of the poll
  ([#58](https://github.com/pounde/FastTAK/pull/58),
  [`6325eb8`](https://github.com/pounde/FastTAK/commit/6325eb890e7a82113230999410802af9630228e8))

- **alerts**: Track state on every call so recoveries land and a recurring warning alerts again
  ([#77](https://github.com/pounde/FastTAK/pull/77),
  [`9bad02b`](https://github.com/pounde/FastTAK/commit/9bad02be4dd3551509fb04e922e77728097ae9d8))

- **caddy**: Publish UDP 443 so the HTTP/3 Caddy advertises is reachable
  ([#122](https://github.com/pounde/FastTAK/pull/122),
  [`9b2e206`](https://github.com/pounde/FastTAK/commit/9b2e206035d92990eae3d77f12895a0928908144))

- **certs**: Resolve Compose through FASTAK_ENV_FILE like the other entry points
  ([#113](https://github.com/pounde/FastTAK/pull/113),
  [`83dfc51`](https://github.com/pounde/FastTAK/commit/83dfc51985a5c8104dd32969a829c3bf31b03a0b))

- **dashboard**: Cert and TLS rows show a real status and the reason a probe failed
  ([#57](https://github.com/pounde/FastTAK/pull/57),
  [`daaaecc`](https://github.com/pounde/FastTAK/commit/daaaecc318d592a888c9cecd594aa5059c9dd710))

- **health**: A cert that cannot be read is an error row, not a missing one
  ([#57](https://github.com/pounde/FastTAK/pull/57),
  [`db9fd45`](https://github.com/pounde/FastTAK/commit/db9fd45d07f08bf113d0767642f61a726f0471e6))

- **health**: The TLS probe reads expiry from the DER cert and reports errors instead of vanishing
  ([#57](https://github.com/pounde/FastTAK/pull/57),
  [`dde50ac`](https://github.com/pounde/FastTAK/commit/dde50ac795624c145131ba9d7028e2244eff5ad6))

- **healthcheck**: Fail when the API answers 5xx, not only when it does not answer
  ([#79](https://github.com/pounde/FastTAK/pull/79),
  [`ccf4854`](https://github.com/pounde/FastTAK/commit/ccf4854bae0b1f085fc37c82aa06882019fceeeb))

- **healthcheck**: Say so when curl or nc is missing
  ([`16b9cab`](https://github.com/pounde/FastTAK/commit/16b9caba2b2ee962ff5d69bb9b4391a8f9230cc9))

- **healthcheck**: Scan the recent log for Ignite disconnects, and stop an old OOM being sticky
  ([#79](https://github.com/pounde/FastTAK/pull/79),
  [`19bd759`](https://github.com/pounde/FastTAK/commit/19bd7596930851879a96b1604489be54ed478e7b))

- **healthcheck**: Startup is not an incident ([#79](https://github.com/pounde/FastTAK/pull/79),
  [`5fca511`](https://github.com/pounde/FastTAK/commit/5fca511f1f30f01bcc9c966cafd549c88e5bfbda))

- **ops**: The SMS test endpoint returns a bool instead of awaiting one
  ([#58](https://github.com/pounde/FastTAK/pull/58),
  [`11bb82f`](https://github.com/pounde/FastTAK/commit/11bb82ff76b1b991f47b01b08bcd8669668b8db2))

- **scheduler**: Call the alert engine on every poll with the evaluator's verdict
  ([#77](https://github.com/pounde/FastTAK/pull/77),
  [`6d03203`](https://github.com/pounde/FastTAK/commit/6d03203eb54e6ceddc9c1924b700d394e304d6f0))

- **start**: A stopped service reads "unknown", not an empty string
  ([`ca480ec`](https://github.com/pounde/FastTAK/commit/ca480ec56f9e3ac9bfb5eb053a0f467d755e2211))

- **start**: Make every failed check say what it checked and what to run
  ([#114](https://github.com/pounde/FastTAK/pull/114),
  [`370942f`](https://github.com/pounde/FastTAK/commit/370942f53e24e7a7db2718fdbdb59b9a46f6e1f4))

- **start**: Treat Compose's "invalid IP:0" as an unpublished port, not port 0
  ([#120](https://github.com/pounde/FastTAK/pull/120),
  [`7988df2`](https://github.com/pounde/FastTAK/commit/7988df2c27978ac88299f681f00b34b4bd8558ec))

- **users**: Build data package truststore in Java layout
  ([`8097b19`](https://github.com/pounde/FastTAK/commit/8097b19a8edb8453cfd546e44b28f8b9dcdc6f85))

### Chores

- Sync uv.lock with the v0.30.3 release
  ([`c75583c`](https://github.com/pounde/FastTAK/commit/c75583c311d3f1e1b1d6b6de65cb0a2b09e81cc8))

### Continuous Integration

- Run pull-request checks for PRs that target dev
  ([`9ca949c`](https://github.com/pounde/FastTAK/commit/9ca949cd37733a70621cf252c468fc2a3489dfda))

### Documentation

- Point operators at just doctor ([#121](https://github.com/pounde/FastTAK/pull/121),
  [`c10b00f`](https://github.com/pounde/FastTAK/commit/c10b00f895965f4fd547db92efbf7490be98870d))

- **decisions**: DD-022 alert flow follows the every-poll engine
  ([#77](https://github.com/pounde/FastTAK/pull/77),
  [`ca2bfc4`](https://github.com/pounde/FastTAK/commit/ca2bfc41c5b6a60c18bc4da953736e9ca7bd5456))

- **decisions**: DD-055 lets an inspection command print its inventory
  ([#121](https://github.com/pounde/FastTAK/pull/121),
  [`d9dbfcf`](https://github.com/pounde/FastTAK/commit/d9dbfcfd8d7e74df01a33fda03548a300b00b69a))

- **decisions**: DD-055 status reporters never fail silent
  ([`d5d5c3c`](https://github.com/pounde/FastTAK/commit/d5d5c3cdead0e529f651c2c3b73b8f76f5288d67))

- **troubleshooting**: The healthy-looking-container symptom is past tense now
  ([#79](https://github.com/pounde/FastTAK/pull/79),
  [`42dd471`](https://github.com/pounde/FastTAK/commit/42dd4710fa28de529b4f93974ca92db8c6c11587))

### Features

- **evaluator**: An item carrying an error is a warning with the reason
  ([#57](https://github.com/pounde/FastTAK/pull/57),
  [`d430ba1`](https://github.com/pounde/FastTAK/commit/d430ba18ef3871dc75bcad017ca9b36212575b14))

- **healthcheck**: Capture the log tails once per incident, where rotation cannot reach
  ([#79](https://github.com/pounde/FastTAK/pull/79),
  [`88e2e8f`](https://github.com/pounde/FastTAK/commit/88e2e8fb8bda6f7a7cf2724be4733d241882ed50))

- **just**: Doctor recipe ([#121](https://github.com/pounde/FastTAK/pull/121),
  [`cffe636`](https://github.com/pounde/FastTAK/commit/cffe636f8198354a3a3aa47c23a3190fea612a35))

- **stack**: The expected published-port set per deploy mode
  ([#121](https://github.com/pounde/FastTAK/pull/121),
  [`07a0dde`](https://github.com/pounde/FastTAK/commit/07a0ddec433914fd1bdff47ae3a534cc18293d69))

- **start**: --doctor checks the running stack without touching it
  ([#121](https://github.com/pounde/FastTAK/pull/121),
  [`58e1eb2`](https://github.com/pounde/FastTAK/commit/58e1eb2413676ab6069379e32857893562360a4d))

- **start**: A failed TAK healthcheck names the incident snapshot
  ([#79](https://github.com/pounde/FastTAK/pull/79),
  [`076353f`](https://github.com/pounde/FastTAK/commit/076353fbefec3b4436bf3b17bf6b268c9063d11b))

- **start**: Add --verbose to print every check ([#114](https://github.com/pounde/FastTAK/pull/114),
  [`f39299d`](https://github.com/pounde/FastTAK/commit/f39299d76f2abb3ffcb6b78dc16882e001dfde4a))

- **start**: Doctor reports the published ports against the deploy mode's set
  ([#121](https://github.com/pounde/FastTAK/pull/121),
  [`fecf9c7`](https://github.com/pounde/FastTAK/commit/fecf9c707d2c25a7aafdf2d0f982ada9f4de8214))

- **start**: The checklist includes the monitor ([#121](https://github.com/pounde/FastTAK/pull/121),
  [`7ffbea4`](https://github.com/pounde/FastTAK/commit/7ffbea416c3dbc703b9fd88cc842399ad56c2b91))

### Refactoring

- **start**: Move the start and checks sections into functions
  ([`3e900f4`](https://github.com/pounde/FastTAK/commit/3e900f47bbe48ed8b4fa1258bd3eecda9bdf2c3a))

### Testing

- **auth**: Every route the gate walk cannot see must be recorded
  ([#83](https://github.com/pounde/FastTAK/pull/83),
  [`c1caf21`](https://github.com/pounde/FastTAK/commit/c1caf21be4207fca9c662843f669b3df8ddc104f))

- **healthcheck**: Drive healthcheck.sh from the environment so it runs outside the container
  ([#79](https://github.com/pounde/FastTAK/pull/79),
  [`178427b`](https://github.com/pounde/FastTAK/commit/178427bee920811095608627c05da7b0ba23c648))

- **integration**: The healthcheck probe answers non-5xx on a healthy stack; document the new
  detection ([#79](https://github.com/pounde/FastTAK/pull/79),
  [`d4929b8`](https://github.com/pounde/FastTAK/commit/d4929b89bd8d334680f1cfce8efe05911e8e1871))

- **start**: A healthy deployment fixture, and prove a healthy stack ends green
  ([`ded742b`](https://github.com/pounde/FastTAK/commit/ded742b90f8267e7d63d8913d18c5e6e0edf3b7b))


## v0.30.3 (2026-09-15)

### Bug Fixes

- **setup**: Print commands that exist, and none for a -d deployment
  ([`5a67eb7`](https://github.com/pounde/FastTAK/commit/5a67eb79f7c1b1d0f94c592326c30c38fc7eb813))


## v0.30.2 (2026-09-15)

### Bug Fixes

- **backup**: Order backups by the timestamp in the filename, not mtime
  ([`51f019b`](https://github.com/pounde/FastTAK/commit/51f019badee203231d857e32ed24f89663039439))

- **security**: Gate the OpenAPI document and Swagger UI behind the admin group
  ([`6562367`](https://github.com/pounde/FastTAK/commit/6562367433011911a41e828f1023079d55313abc))

### Documentation

- Add User Types to the mkdocs nav
  ([`e3ce747`](https://github.com/pounde/FastTAK/commit/e3ce747feb394df329f8df027e77416da1c52597))

- **env**: Stop claiming FASTAK_DB_PASSWORD is generated
  ([`12e4fe9`](https://github.com/pounde/FastTAK/commit/12e4fe99b14e508081d80cecc9dc5936dc11a592))


## v0.30.1 (2026-09-15)

### Bug Fixes

- **restore**: Keep the host's TAK_VERSION; refuse below-floor archives
  ([`dd9ce30`](https://github.com/pounde/FastTAK/commit/dd9ce30c22348ed9084de975ea30b720cb41dabc))

- **security**: Request partial-chain verification explicitly
  ([`644ca4e`](https://github.com/pounde/FastTAK/commit/644ca4e83fa677d3206268a826ac7d454d8f18ff))

- **security**: Verify TAK Server's chain against the deployment CA
  ([`48705bb`](https://github.com/pounde/FastTAK/commit/48705bbe27810e425b2a0541ab7efbdab1edb36d))


## v0.30.0 (2026-09-14)

### Bug Fixes

- Correct just --list descriptions, targeted-start admin URL, and test-stack down guard
  ([`7369d48`](https://github.com/pounde/FastTAK/commit/7369d48e31d8705de9f7976ad470adec71b95340))

- **start**: Print real commands in the closing hints
  ([`5fd9cfc`](https://github.com/pounde/FastTAK/commit/5fd9cfcebb8557a037fbdff4725ec9278eac956d))

- **start**: Stop advertising docker compose down -v after a healthy start
  ([`f1285a2`](https://github.com/pounde/FastTAK/commit/f1285a24cfe9cdbcc47431d42f0d495cadc387a7))

- **start**: Stop on a failed build or up instead of reporting success
  ([`b652e1a`](https://github.com/pounde/FastTAK/commit/b652e1aa8652002ba28e89310442dd246d4826c6))

### Chores

- Sync uv.lock to 0.29.2
  ([`5fd97f5`](https://github.com/pounde/FastTAK/commit/5fd97f537e83e1e7e9f4b39e4376397c63678ef1))

### Documentation

- Point every workflow at its just recipe
  ([`f45cd03`](https://github.com/pounde/FastTAK/commit/f45cd03c8e4c51308b92f7469ef4b0dc5720ca8b))

- Point operator instructions at just, not the scripts directly
  ([`bff6ef4`](https://github.com/pounde/FastTAK/commit/bff6ef464d097862bfdedce64f8fbe4055a092bc))

- **backup**: Name the run subcommand
  ([`7c69108`](https://github.com/pounde/FastTAK/commit/7c69108b23a0f25ce7ab867c007db0a51d207952))

- **just**: Carry each recipe's full doc block in its [doc] attribute
  ([`c97153f`](https://github.com/pounde/FastTAK/commit/c97153f1598414943f2e95324bca4128ef522db4))

- **just**: Document each recipe's workflow and arguments, and test it
  ([`0ae2f19`](https://github.com/pounde/FastTAK/commit/0ae2f1931c582cab8272f25511f4eabd14321cca))

### Features

- **env**: Detect .env drift in both directions
  ([`60ac9d2`](https://github.com/pounde/FastTAK/commit/60ac9d2c63e651b213ec3c409a0c120ff0d80685))

- **just**: Add setup recipe; bare just lists the recipes
  ([`3ba9152`](https://github.com/pounde/FastTAK/commit/3ba9152d1182aec385a487cfcfb8c62ab5d0c84c))

- **scripts**: Add lib-stack.sh for compose-file resolution
  ([`3983d75`](https://github.com/pounde/FastTAK/commit/3983d7531da0def4a52185ba213d7ed94e8b2a9a))

- **scripts**: Answer --help everywhere; bare setup.sh prints usage
  ([`8b22fd8`](https://github.com/pounde/FastTAK/commit/8b22fd835cb01f1e22635d7db234b0158c59efc0))

- **start**: Accept services, --capture, --checks and --no-wait
  ([`9cf5e1e`](https://github.com/pounde/FastTAK/commit/9cf5e1e87c09efc2f759d2c321df0c75bc85a9e6))

### Refactoring

- **just**: Collapse backup/backups/backup-prune into one passthrough
  ([`4676bb6`](https://github.com/pounde/FastTAK/commit/4676bb686954a3b4e00557087e5071d19d8392de))

- **start**: Delete --test, port its remaining assertions
  ([`ddd0d5b`](https://github.com/pounde/FastTAK/commit/ddd0d5b62663da0fce69a98453fd1f4117d6bc51))

- **start**: Resolve compose files through lib-stack.sh
  ([`cf4451a`](https://github.com/pounde/FastTAK/commit/cf4451a4c94764a4a80d917682e65b2880d6a111))

- **test**: One CLI for the integration stack; fast suite to a script
  ([`504b69a`](https://github.com/pounde/FastTAK/commit/504b69ae6f13a0a9614c418d93f00690e255a33b))

### Testing

- **start**: Assert the compose-file prefix, not the exact value
  ([`0bb1c4f`](https://github.com/pounde/FastTAK/commit/0bb1c4f5e9e0379168ec2b3e7c0f375163dc193e))


## v0.29.2 (2026-08-26)

### Bug Fixes

- **setup**: Stop advertising an upgrade path that does not exist
  ([`d1c8cdf`](https://github.com/pounde/FastTAK/commit/d1c8cdf3bdc72add12c76e9c8ada5ccf0ddece72))

### Documentation

- **readme**: Document how to update FastTAK itself
  ([`f3e55d8`](https://github.com/pounde/FastTAK/commit/f3e55d8d89fc3fc20aa6253cb809426c3ca94ed7))


## v0.29.1 (2026-08-26)

### Bug Fixes

- **start**: Stop reporting failures on a healthy stack
  ([`d324671`](https://github.com/pounde/FastTAK/commit/d3246713262423a259cdb457718de255ffd09545))

### Chores

- **ci**: Pin gitPython version
  ([`5bb7f06`](https://github.com/pounde/FastTAK/commit/5bb7f06d29b0a682bc6e66bd3ada1050e1c0afc5))


## v0.29.0 (2026-08-25)

### Bug Fixes

- **ci**: Let the pre-commit shellcheck hook follow sourced files
  ([`1ae9ccd`](https://github.com/pounde/FastTAK/commit/1ae9ccdda6357a5858781d694446588ec37dacba))

- **database**: Apply tuning via postgresql.conf and verify it took effect
  ([`e13688c`](https://github.com/pounde/FastTAK/commit/e13688c1d43a7283448bd6f110b7f91a99b57a1c))

- **database**: Harden CoreConfig rewrite and shutdown reporting in start.sh
  ([`46085a0`](https://github.com/pounde/FastTAK/commit/46085a0b940bb3b0eff58f7bc0971a0a6bb72259))

- **database**: Let tak-database start under TAK 5.8
  ([`e0c82fe`](https://github.com/pounde/FastTAK/commit/e0c82fe78b448b854046aed57c832ac5f3b769a5))

- **database**: Pin app-db PGDATA and wire the persistence guard into it
  ([`01c2b23`](https://github.com/pounde/FastTAK/commit/01c2b232025522f24cb05562af339f031224c69d))

- **database**: Write tuning to the live config, not just the vendor's copy
  ([`92a49f7`](https://github.com/pounde/FastTAK/commit/92a49f716495a90920a6d32aae68161d2a616b01))

- **preflight**: Distinguish unparseable TAK_VERSION from below-floor
  ([`7fa35be`](https://github.com/pounde/FastTAK/commit/7fa35bed91cd8e9bb63376d48707355359786d6d))

- **setup**: Abort on failed docker build instead of masking it
  ([`395531f`](https://github.com/pounde/FastTAK/commit/395531ff86ed40f490510901142b7e7491e84129))

- **tak-server**: Match TAK processes via /proc, not pgrep
  ([`b44d767`](https://github.com/pounde/FastTAK/commit/b44d7677287ee6b3cc64111de2904b6caa384e05))

- **test**: Repair su regression and flaky legacy-app-db rehearsal helper
  ([`37b4f09`](https://github.com/pounde/FastTAK/commit/37b4f09c7364e9eda222349371e9e983a6eec318))

- **upgrade**: Create the cot dump's extensions as a superuser
  ([`fafd39b`](https://github.com/pounde/FastTAK/commit/fafd39bec91dceb4525d7dccf8c7d19e3b0dcfa8))

- **upgrade**: Keep the direct-mode ports and the CoT history
  ([`ec8a452`](https://github.com/pounde/FastTAK/commit/ec8a452aa7dcc0acade64d26b39158b05a3a6099))

- **upgrade**: Restore CoT from what was destroyed, not from a version gap
  ([`6a31e62`](https://github.com/pounde/FastTAK/commit/6a31e62873b079d9056c6a7b37e6e607d7574ffa))

- **upgrade**: Stop advising the command that destroys the CoT history
  ([`b777bbb`](https://github.com/pounde/FastTAK/commit/b777bbb045ff4fe16df7bb43c00815c91ad36450))

- **upgrade**: Take the project name from Compose and stop psql lying
  ([`3617940`](https://github.com/pounde/FastTAK/commit/3617940992deb995890150da452c1556c5ed2c21))

### Documentation

- Document the 5.8 upgrade, disk headroom, and DD-051..054
  ([`3902cf4`](https://github.com/pounde/FastTAK/commit/3902cf4483144fee94b42dbf895881438940a038))

- Settle the docs on a 5.8 floor
  ([`d23460e`](https://github.com/pounde/FastTAK/commit/d23460e3a899b3f7ac580be20a98c4a4488f79d4))

### Features

- **database**: Add a guard asserting PGDATA is on a mounted volume
  ([`e868f84`](https://github.com/pounde/FastTAK/commit/e868f8467b2961af537476529594d6275e8922de))

- **preflight**: Reject a TAK_VERSION below the 5.8 floor
  ([`1a28887`](https://github.com/pounde/FastTAK/commit/1a288872d55937b71ed86c8999c85ccb3bd46d88))

- **setup**: Add TAK Server version parsing and floor comparison
  ([`94fc669`](https://github.com/pounde/FastTAK/commit/94fc6690125fdd4724c418e7b25b92b7214a879b))

- **setup**: Require the hardened TAK Server 5.8 bundle or later
  ([`d2a8e98`](https://github.com/pounde/FastTAK/commit/d2a8e98d3e43e01255e758721cdef315bd17bafe))

- **stack**: Move app-db and the monitor's pg client to PostgreSQL 18
  ([`4b9c535`](https://github.com/pounde/FastTAK/commit/4b9c53577f102e575da4919817ff4211009d1108))

- **upgrade**: Add just upgrade with PostgreSQL major migration
  ([`c98cfd1`](https://github.com/pounde/FastTAK/commit/c98cfd12d402fb8b89bc496fdd83974925392702))

### Refactoring

- Drop the automated cross-major database upgrade
  ([`869093c`](https://github.com/pounde/FastTAK/commit/869093ccb870d953e391c38de9f1d127d8ded4f4))

### Testing

- Isolate the destructive upgrade rehearsal from the shared test stack
  ([`a5db6b2`](https://github.com/pounde/FastTAK/commit/a5db6b20805a7cbc14facfd5c7ce3ea0500d819d))

- **database**: Strengthen proof that pgdata guard requires exact mount match
  ([`ee15033`](https://github.com/pounde/FastTAK/commit/ee15033c5949acd539b8193ff10bacaba79dce42))

- **upgrade**: Rehearse just upgrade against a live stack
  ([`d3ae36f`](https://github.com/pounde/FastTAK/commit/d3ae36fcc278ea2a6131b14613c325641fb165ff))


## v0.28.4 (2026-08-14)

### Bug Fixes

- **monitor**: Log when the ldap-proxy shared secret is missing
  ([`3d0ce07`](https://github.com/pounde/FastTAK/commit/3d0ce0740dfd7da8e26852d2caeb1ba4cfd955be))

- **security**: Authenticate ldap-proxy /tokens and reject anonymous search
  ([#54](https://github.com/pounde/FastTAK/pull/54),
  [`e26fb00`](https://github.com/pounde/FastTAK/commit/e26fb00a96111f9ca65c507d869441a49e252900))

- **upgrade**: Provision required secrets for existing deployments
  ([`d461205`](https://github.com/pounde/FastTAK/commit/d461205d860f7c7d91133a40d22f25beb345fdf7))

### Documentation

- **decisions**: DD-050's testing note now describes reality
  ([`f501bf2`](https://github.com/pounde/FastTAK/commit/f501bf2e8b917e275b773944b5e48f6cb7e55ca7))

- **decisions**: Record the ldap-proxy authz decision and correct DD-036 (DD-050)
  ([`ce3a001`](https://github.com/pounde/FastTAK/commit/ce3a0019d892e1b9ff9fc4058fabad8f3d9c62e4))

### Testing

- **ci**: Run ldap-proxy's authorization tests
  ([`d37e947`](https://github.com/pounde/FastTAK/commit/d37e9476cd5ab8a41eb620aa664e44ed8e90a9c7))


## v0.28.3 (2026-08-14)

### Bug Fixes

- **security**: Defer the non-root monitor; keep the socket proxy
  ([`4e01d9a`](https://github.com/pounde/FastTAK/commit/4e01d9a56a1a3bada8afe4033b47f4f93146fb35))

- **security**: Make the docker-proxy allowlist match what the monitor calls
  ([`955f518`](https://github.com/pounde/FastTAK/commit/955f5182bde3e944639b32687534badff473385a))

- **security**: Scope monitor Docker access via socket proxy, run non-root
  ([#53](https://github.com/pounde/FastTAK/pull/53),
  [`74a3b1e`](https://github.com/pounde/FastTAK/commit/74a3b1e37b9f3cb42d313e9644a3e160f3d5d2c3))

### Documentation

- **decisions**: Record the Docker socket proxy decision (DD-049)
  ([`864217b`](https://github.com/pounde/FastTAK/commit/864217b8686f4eaa29286922bb5b1de88e7c9ec7))


## v0.28.2 (2026-08-14)

### Bug Fixes

- **upgrade**: Enforce upgrade rules in the scripts; document the rest (DD-048)
  ([`9f87912`](https://github.com/pounde/FastTAK/commit/9f8791269903925bc1def40f537a81671f68d41b))

### Documentation

- Fix formatting...again
  ([`baab69a`](https://github.com/pounde/FastTAK/commit/baab69a5bae969ff5eb017d79cdca6ba76925df6))

- Fix warning formatting in authentication.md
  ([`e870d36`](https://github.com/pounde/FastTAK/commit/e870d36a1f7733fdbe81ffa42d474cc18563c966))


## v0.28.1 (2026-08-11)

### Bug Fixes

- **security**: Gate admin APIs and fix silent TTL revocation skip
  ([`8a0179e`](https://github.com/pounde/FastTAK/commit/8a0179e1b77123fd0d2acc4a256fa1b810ad0ec3))

- **security**: Lock the monitor down to admin-only (DD-047)
  ([`78ecfd2`](https://github.com/pounde/FastTAK/commit/78ecfd279385bd0fbb8491162fd6b371eb4eb38a))

- **test**: Give docker compose the env it needs to interpolate the test overlay
  ([`0787f3b`](https://github.com/pounde/FastTAK/commit/0787f3b2e808576cda56541414cd437c848265dd))


## v0.28.0 (2026-07-08)

### Bug Fixes

- Preserve docker-compose.override.yml; document federation as disabled
  ([`f4c32cb`](https://github.com/pounde/FastTAK/commit/f4c32cba654d3d927ab457f4ff11c1c60aa78462))

### Documentation

- **capture**: Capture guide, nav entry, decision record DD-045
  ([`5a6e4ef`](https://github.com/pounde/FastTAK/commit/5a6e4eff0c7ff5f542c23e5522f823d10ba0a14e))

### Features

- **capture**: Mitmproxy traffic-capture overlay + --capture flag
  ([#49](https://github.com/pounde/FastTAK/pull/49),
  [`7766786`](https://github.com/pounde/FastTAK/commit/77667863c01059bc0fdf14c001f3b77b8cc3d962))

### Testing

- **capture**: End-to-end integration tests for the capture overlay
  ([`330d860`](https://github.com/pounde/FastTAK/commit/330d860efd7642eae03217f496a16e8f8d4d5a43))


## v0.27.2 (2026-06-10)

### Bug Fixes

- **dronesense**: Reconcile MediaMTX proxy paths instead of add-once
  ([`006c96f`](https://github.com/pounde/FastTAK/commit/006c96fc21898d6b13de406601eb25721ec79e45))

### Chores

- Fix formatting of .env.example
  ([`3028bce`](https://github.com/pounde/FastTAK/commit/3028bce535600c910f5a7d6df8808d8286dbb349))

- Sync stale uv.lock to 0.27.1
  ([`57acd72`](https://github.com/pounde/FastTAK/commit/57acd729b2d22a30bac6d883dc7774b64d781bb3))


## v0.27.1 (2026-05-28)

### Bug Fixes

- Increase takserver and tak-database resources to prevent crashes
  ([`83dffeb`](https://github.com/pounde/FastTAK/commit/83dffeb534223e331fbd5dc1675ddcf5e289ad59))


## v0.27.0 (2026-05-28)

### Bug Fixes

- **ci**: Gate release on unreleased commits, not just pushed files
  ([`1db5a0f`](https://github.com/pounde/FastTAK/commit/1db5a0faaa256894c452cd3b029bd385f4ffaa8a))

- **test**: Stub age-keygen in backup runner unit tests
  ([`5fddb12`](https://github.com/pounde/FastTAK/commit/5fddb12743fe1e9d12aa4f5c0f4819fe7a4d11b2))

### Features

- Backup and restore
  ([`fbb8208`](https://github.com/pounde/FastTAK/commit/fbb82081a919efdad21dd8511b512503b0aab506))

- Optional MediaMTX stream recording
  ([`1e6e245`](https://github.com/pounde/FastTAK/commit/1e6e2451389d2316333852957aec8317cb50be8b))

### Refactoring

- Remove TAK Portal
  ([`cd53982`](https://github.com/pounde/FastTAK/commit/cd539822e33659e4eb3031565cdfc0f79d438bab))


## v0.26.0 (2026-05-11)

### Features

- **env**: Expose LKP_REQUIRE_TAK_CLIENT via .env and compose
  ([`537d07f`](https://github.com/pounde/FastTAK/commit/537d07fbd125e083be7c6e554888fec703c6924f))

- **tak**: Filter passive feeds from recent contacts via endpoint gate
  ([`aca551c`](https://github.com/pounde/FastTAK/commit/aca551c2f9b66b59410ad0ae02ddb0a9dc4406e9))


## v0.25.0 (2026-05-03)

### Documentation

- Align route doc, decisions log, and orphaned reference
  ([`c8b4aba`](https://github.com/pounde/FastTAK/commit/c8b4aba9119cfe65782525c231a7beef75ae4c50))

- **tak**: Fix max_age Query description for new 24h default
  ([`a812b91`](https://github.com/pounde/FastTAK/commit/a812b916e426db4b4d1231a0dca2e9c2bebc277f))

### Features

- **config**: Add LKP_COT_TYPE_PREFIXES setting
  ([`c8b89e2`](https://github.com/pounde/FastTAK/commit/c8b89e21ff51329d19cddc7cb8c4bceb8b799e7a))

- **dashboard**: Window dropdown on Recently seen card
  ([`4393c24`](https://github.com/pounde/FastTAK/commit/4393c24ea961df4637c196cb3335eb124a342b8b))

- **env**: Expose LKP_COT_TYPE_PREFIXES via .env and compose
  ([`7f68c43`](https://github.com/pounde/FastTAK/commit/7f68c43b85a4716385e7f40fb64c62eb3d8aa766))

- **tak**: _parse_detail extracts callsign/team/role from CoT XML
  ([`95f2f37`](https://github.com/pounde/FastTAK/commit/95f2f37a0bcb4677e9eb4ddf3e875c32ccac0b93))

- **tak**: Get_recent_lkp queries cot_router for window of UIDs
  ([`721f1ec`](https://github.com/pounde/FastTAK/commit/721f1ec6e2363796df7cb499b3973eb424480f9e))

### Refactoring

- **dashboard**: Tighten window dropdown UX and test
  ([`7a44f73`](https://github.com/pounde/FastTAK/commit/7a44f73f4ce841b40075916089a3e1ea3b3ac8b8))

- **tak**: Drive recent-contacts from cot_router instead of /contacts/all
  ([`7cdb6ce`](https://github.com/pounde/FastTAK/commit/7cdb6ceb07ef2af6f9b0250221cd51ddd9b2703e))

- **tak**: Drop unreachable error handling in _parse_detail
  ([`7508eef`](https://github.com/pounde/FastTAK/commit/7508eef35cc6b1f2ddf54c60322a344c0d4ef0ae))

- **tak**: Tighten recent-contacts code and test for HTTP-503 path
  ([`08603da`](https://github.com/pounde/FastTAK/commit/08603da4d9d9e72555de6b9df4786c7fd3dcf3a4))

### Testing

- **integration**: /api/tak/contacts/recent surfaces cot_router UIDs
  ([`a3482be`](https://github.com/pounde/FastTAK/commit/a3482bea9c6406fb4eeae6a2d6733de9d60ccda2))

- **integration**: Document _psql constraints and surface cleanup errors
  ([`16bdef7`](https://github.com/pounde/FastTAK/commit/16bdef754c990f2245bab74e713da021f0bb3e81))


## v0.24.2 (2026-05-03)

### Bug Fixes

- **init-config**: Backfill x509useGroupCacheRequiresExtKeyUsage on existing auth
  ([`7ae338d`](https://github.com/pounde/FastTAK/commit/7ae338d9fe664d687e35bc631b3fce5c694dfcb5))

- **init-config**: Move backfill outside LDAP guard so it runs unconditionally
  ([`5464140`](https://github.com/pounde/FastTAK/commit/54641400c02c1ad2cf2a4201c074dcce07a334c3))

- **init-config**: Set x509useGroupCacheRequiresExtKeyUsage=false on fresh install
  ([`3fa34c5`](https://github.com/pounde/FastTAK/commit/3fa34c55e53a9d0308dbbbbf20372ef02a77b6bc))

### Documentation

- **decisions**: Add DD-042 disabling x509 EKU requirement for group cache
  ([`2b8f697`](https://github.com/pounde/FastTAK/commit/2b8f69744cd9dee0f6a71d6f14b95fd5cf43fc83))

### Testing

- **integration**: Add channel filter regression tests
  ([`7dcdb4a`](https://github.com/pounde/FastTAK/commit/7dcdb4a50498f9e65f8a9ba709b386a086594cb1))

- **integration**: Use webadmin in channel-filter tests to avoid soft-delete pollution
  ([`86b9a9a`](https://github.com/pounde/FastTAK/commit/86b9a9ac59146c2ae3976252a13db5b4fdffdda2))


## v0.24.1 (2026-04-30)

### Bug Fixes

- **nodered**: Update DS -> CoT urls
  ([`4820f1d`](https://github.com/pounde/FastTAK/commit/4820f1d888081cf6b0ee6bf50007499f2aca7946))


## v0.24.0 (2026-04-29)

### Features

- **nodered**: Emit t-x-d-d deletes for canceled NOAA alerts
  ([`20b6693`](https://github.com/pounde/FastTAK/commit/20b66939c7cf39d74c5706f15b16118101bf7608))


## v0.23.0 (2026-04-28)

### Bug Fixes

- **caddy**: Forward Remote-User/Remote-Groups to monitor route
  ([`ff3190b`](https://github.com/pounde/FastTAK/commit/ff3190bacb92a9fda1494c92d23ed626b69fb0e8))

- **tak**: Decode SQL_ASCII bytes from cot_router to str
  ([`30066c7`](https://github.com/pounde/FastTAK/commit/30066c73600e513a3ab72792b8bcd6aa8600dc04))

### Chores

- Address full-branch review follow-ups
  ([`2159d4b`](https://github.com/pounde/FastTAK/commit/2159d4b87a1b5756dcf974e0dd3cc33790c6565a))

- **audit**: Address final-review follow-ups
  ([`249bbc8`](https://github.com/pounde/FastTAK/commit/249bbc8be9ccf49d38e5752b5d7e330b1fe04cbe))

### Documentation

- **api**: Add Google-style docstrings for OpenAPI generation
  ([`3657455`](https://github.com/pounde/FastTAK/commit/3657455243491164f980279fd4773ffb874fd5ad))

- **decisions**: Record audit + persistent event store decision
  ([`90f5d79`](https://github.com/pounde/FastTAK/commit/90f5d79e625a8fd40161309c7455344100700580))

- **decisions**: Record TAK Server proxy endpoints decision
  ([`6567a69`](https://github.com/pounde/FastTAK/commit/6567a69c98ce5491d42ac769aaab2416eb6d3c18))

### Features

- **api**: Add /api/events query endpoint
  ([`5276b1c`](https://github.com/pounde/FastTAK/commit/5276b1c29c9a47ee2a8f253028743b6f336a6758))

- **api**: Add /api/events.csv export
  ([`0b1da8e`](https://github.com/pounde/FastTAK/commit/0b1da8e709582cdd0f76ceeecceb24494bffff42))

- **api**: Add /api/tak router with /groups proxy endpoint
  ([`d341cd7`](https://github.com/pounde/FastTAK/commit/d341cd7633f2910a5959bf37a40a750b19679390))

- **api**: Add /api/tak/clients endpoint
  ([`097d976`](https://github.com/pounde/FastTAK/commit/097d976f92d5f8660790c8f7bc7a700ee266091b))

- **api**: Add /api/tak/contacts endpoint
  ([`db67a5e`](https://github.com/pounde/FastTAK/commit/db67a5e3c871968b891f28d371581e6832c91250))

- **api**: Add /api/tak/contacts/recent with optional max_age
  ([`a01165f`](https://github.com/pounde/FastTAK/commit/a01165f3f5871f5dcb5a3065a6e90c44c2ee4b74))

- **api**: Add /api/tak/missions endpoint
  ([`93323c7`](https://github.com/pounde/FastTAK/commit/93323c73550c5adcde587b28dec09dc43bff2385))

- **app-db**: Create fastak database for audit/events store
  ([`6a47bbe`](https://github.com/pounde/FastTAK/commit/6a47bbe5ee6ae9f9caaeae23c10df8a06f02020c))

- **audit**: Add record_event for fire-and-forget event inserts
  ([`ce546fb`](https://github.com/pounde/FastTAK/commit/ce546fb30c0ddfff5f8d16ae33d5214a58a47f04))

- **audit**: Create fastak_events table on monitor startup
  ([`1a69768`](https://github.com/pounde/FastTAK/commit/1a697684bcc7ae0ede749ec303051cd3d4ae1934))

- **audit**: Middleware records mutating API calls to fastak_events
  ([`1a11211`](https://github.com/pounde/FastTAK/commit/1a112116808eecc75a76dbc29edf9e51ab97e776))

- **dashboard**: Add connected-clients and recent-contacts cards
  ([`af916df`](https://github.com/pounde/FastTAK/commit/af916dfa86a9079c38ef0d017c334c395c58754b))

- **dashboard**: Connected-clients partial with LKP
  ([`627a094`](https://github.com/pounde/FastTAK/commit/627a094c2e33a2782d4af975c5dae1e0b9c308ac))

- **dashboard**: Recent-contacts partial with explicit max_age
  ([`98bac2f`](https://github.com/pounde/FastTAK/commit/98bac2f88fff3fb0381bb4b2d131b39221e2ab38))

- **db**: Query/execute accept params tuple for safe SQL
  ([`bdd82cb`](https://github.com/pounde/FastTAK/commit/bdd82cbb108ddd3de72ff10f69313fa1de3f35ba))

- **monitor**: Add fastak_db connection helper
  ([`95cb137`](https://github.com/pounde/FastTAK/commit/95cb1376e35d2e2c986d76cc0d8e7c3d94fb9112))

- **monitor**: Auth-context middleware exposes Remote-User/Remote-Groups
  ([`7c6a42b`](https://github.com/pounde/FastTAK/commit/7c6a42b25cdd3058f70185171b7432955788934b))

- **monitor**: Persist health activity log to fastak_events
  ([`4350c43`](https://github.com/pounde/FastTAK/commit/4350c434227c306ce3b12e2e48dd177267d6a9fd))

- **tak**: Add cot_router-backed LKP query helpers
  ([`a4d37e7`](https://github.com/pounde/FastTAK/commit/a4d37e7963076c1d211c8b2149e068f638c60caf))

- **tak**: Hide service-account contacts from /api/tak/contacts*
  ([`29ddb10`](https://github.com/pounde/FastTAK/commit/29ddb10b4d41e80aec2c6aac15ab330bd74edbd4))

- **tak**: Hide service-account subscriptions from /api/tak/clients
  ([`3d23e1d`](https://github.com/pounde/FastTAK/commit/3d23e1d844c5b3b93f8ff27b0f2bd07d4a22f1b6))

- **tak-client**: Add list_clients() wrapping subscriptions/all
  ([`ad33f74`](https://github.com/pounde/FastTAK/commit/ad33f7437932f2529fee04edd50c325d309360c2))

- **tak-client**: Add list_contacts() wrapping /Marti/api/contacts/all
  ([`29f1173`](https://github.com/pounde/FastTAK/commit/29f117380f20ee50ee44846a6e927b3d130fa6ac))

- **tak-client**: Add list_missions() wrapping /Marti/api/missions
  ([`1429472`](https://github.com/pounde/FastTAK/commit/1429472380cd29bbb06836e698cfcb578a46d385))

### Testing

- **api**: Exercise /api/tak/clients?include=lkp enrichment
  ([`369ed25`](https://github.com/pounde/FastTAK/commit/369ed25fd784d986e6436939f7dc7f562dae2f7b))

- **db**: Align params tests with existing class-based style
  ([`6cdf870`](https://github.com/pounde/FastTAK/commit/6cdf8707fb7d62604e3d477f3ff937d6f1f9fe03))

- **integration**: /api/tak/* proxy endpoints
  ([`5f38275`](https://github.com/pounde/FastTAK/commit/5f382755e85c28d910ca92a5e7fa35fb2f60b97c))

- **integration**: Audit middleware persists to fastak_events
  ([`561872b`](https://github.com/pounde/FastTAK/commit/561872bee8ef3cc690f05165906ff3bc40240445))


## v0.22.0 (2026-04-26)

### Features

- **nodered**: TAK CoT flow library + pipelines
  ([`27cc849`](https://github.com/pounde/FastTAK/commit/27cc849a4b6666bfca09f9c4f77f13b96d60100e))


## v0.21.0 (2026-04-24)

### Features

- **nodered**: DroneSense flow — drones, phones w/ video, operators
  ([`0169326`](https://github.com/pounde/FastTAK/commit/0169326828afb7092aee49a61ff81cf60d1a661d))

- **nodered**: Per-account PEM pipeline for Node-RED TLS
  ([`13c3505`](https://github.com/pounde/FastTAK/commit/13c350544ad46038b54108b628af15e2eb0d4687))

- **nodered**: TLS config UX polish and bootstrap cleanup
  ([`e630f49`](https://github.com/pounde/FastTAK/commit/e630f490325701484257dc8a49f42461c82e25fd))


## v0.20.1 (2026-04-21)

### Bug Fixes

- Bind tak-server 8446 on host in base compose (DD-038)
  ([`f053307`](https://github.com/pounde/FastTAK/commit/f0533071f6f9e9637b27f6a34422f49b005e63cf))

- **ldap-proxy**: Rate limit counts only failed auth attempts (DD-037)
  ([`1fbb57d`](https://github.com/pounde/FastTAK/commit/1fbb57d813ff6d0750c8fe975371a5137a4c71e9))

- **monitor**: Skip certmod registration on cert generation
  ([`bb1a738`](https://github.com/pounde/FastTAK/commit/bb1a7389f9c6e27012bb4597142ae1f39daf0fa4))

- **monitor**: Switch bulk cert revocation to CRL path
  ([`6f17864`](https://github.com/pounde/FastTAK/commit/6f17864d54725b39454b4172c0f79b0c46256a93))

### Chores

- **monitor**: Remove dead register_cert helper
  ([`a4566cc`](https://github.com/pounde/FastTAK/commit/a4566cc443f6c574c0a6c54c7c400fbf12cd2c13))

### Documentation

- Add AWS Lightsail deployment walkthrough
  ([`4c5312f`](https://github.com/pounde/FastTAK/commit/4c5312fb0f43e2ec8dfab17fd54fe4b643303dce))


## v0.20.0 (2026-04-18)

### Documentation

- Document LDAP rate limit (DD-035)
  ([`61ac509`](https://github.com/pounde/FastTAK/commit/61ac5095d3244f75d4358ddf11be1ca885ac3ab2))

### Features

- Switch app-db to official postgres image for ARM support
  ([`0161f4c`](https://github.com/pounde/FastTAK/commit/0161f4cc4bb41df86f49b5da23588e2d94fe7099))

- **ldap-proxy**: Add in-memory sliding-window rate limiter
  ([`4af1a40`](https://github.com/pounde/FastTAK/commit/4af1a401596ede1d8ca196d0f135996cf4434c4a))

- **ldap-proxy**: Env-configurable rate limit defaults
  ([`c8e7ef7`](https://github.com/pounde/FastTAK/commit/c8e7ef769cdd2b42f70895c0093ef4fde05439ba))

- **ldap-proxy**: Wire rate limiter into /auth/verify
  ([`a1f8fe8`](https://github.com/pounde/FastTAK/commit/a1f8fe8d57b4a8c94f26efea5d34f2a64d74131d))

### Testing

- **integration**: Verify /auth/verify returns 429 after rate limit
  ([`57bf455`](https://github.com/pounde/FastTAK/commit/57bf4551ace0f445b277ec186efd433b10d7b798))


## v0.19.0 (2026-04-18)

### Documentation

- Document container memory caps (DD-034)
  ([`23a0000`](https://github.com/pounde/FastTAK/commit/23a0000bed515205f809bd6bddf5148b6bcd7517))

- Update DD numbering
  ([`4d5b8a6`](https://github.com/pounde/FastTAK/commit/4d5b8a6d288e80926558a2dc999c7094066e9da8))

### Features

- Cap container memory per service
  ([`c09b117`](https://github.com/pounde/FastTAK/commit/c09b117b3059801036b3359bc4345ba4e1076291))


## v0.18.0 (2026-04-18)

### Bug Fixes

- Deterministic-length password generator + banner alignment
  ([`298fc7e`](https://github.com/pounde/FastTAK/commit/298fc7efa6f16393c233e13d5279e1c71374df84))

- Harden .env parser against export prefix, whitespace, inline comments
  ([`bef1a53`](https://github.com/pounde/FastTAK/commit/bef1a539d7be1b1ef9e08474f17694a301a53d7f))

- Make .env parsing robust against quotes, duplicates, and = in values
  ([`fb20196`](https://github.com/pounde/FastTAK/commit/fb201963f8da1a9b44e0e0257b6a95c53782f282))

### Documentation

- Add ATAK-CIV 5.7 source code types to CoT registry (source S4)
  ([`f5ec711`](https://github.com/pounde/FastTAK/commit/f5ec7118c651043523d031c4ef7b1e5a0faf5e39))

- Add dfpc-coe custom drawing types to CoT registry (source S3)
  ([`052f71c`](https://github.com/pounde/FastTAK/commit/052f71c98baaf5240f6c4a46bb1f08ebce33c8b0))

- Add MITRE CoT baseline files (CoTtypes.xml v1.80, types.txt v1.5)
  ([`b0ad8c8`](https://github.com/pounde/FastTAK/commit/b0ad8c8b87776dfde30156cb2d609eabfb431ba9))

- CoT registry 2026 consolidated edition — cleanup and full paths
  ([`f312abb`](https://github.com/pounde/FastTAK/commit/f312abbe5def5e2b6d2393cc015399b8a734d66e))

- Document per-install admin password (DD-033)
  ([`14c2433`](https://github.com/pounde/FastTAK/commit/14c24332c052ad86dafd99228aab906f4dfd7ec2))

- Generate CoT reference page at MkDocs build time
  ([`06687e9`](https://github.com/pounde/FastTAK/commit/06687e9c798eeb39467fdc20332c18b16fbfdcb5))

- Reformat CoTtypes.xml to multi-line attributes for readable diffs
  ([`ecce446`](https://github.com/pounde/FastTAK/commit/ecce4465e06f9fdb0920bfb723997bee4126a828))

- Remove residual FastTAK-Admin-1! references
  ([`3eb2868`](https://github.com/pounde/FastTAK/commit/3eb2868585b50ca50c7d01882bf4c1eb7475192d))

- Update README
  ([`3c640e0`](https://github.com/pounde/FastTAK/commit/3c640e09189cb900f7390dd384bbf13e38a578a9))

### Features

- Add .env preflight validator with default-password gate
  ([`3ccade8`](https://github.com/pounde/FastTAK/commit/3ccade821ba52e54a0f376e0feed110e6c947940))

- Setup.sh auto-generates TAK_WEBADMIN_PASSWORD
  ([`82b5b63`](https://github.com/pounde/FastTAK/commit/82b5b63e9985805f6aae3e57dd7e6e6b589b72d8))

- Start.sh calls scripts/check-env.sh for preflight validation
  ([`1461f04`](https://github.com/pounde/FastTAK/commit/1461f04cb7f53f9bebcae795b2bee794aa09c165))

- Switch app-db to official postgres image for ARM support
  ([`8e980f2`](https://github.com/pounde/FastTAK/commit/8e980f217d1c0f182b2a8b89be5efbb27d6444c3))


## v0.17.1 (2026-04-14)

### Performance Improvements

- **test**: Replace Docker exec transport with direct httpx, tighten healthcheck
  ([`e450658`](https://github.com/pounde/FastTAK/commit/e450658cb83bb959a6860ca95f8274e3999c7bf0))


## v0.17.0 (2026-04-14)

### Bug Fixes

- **bootstrap**: Register fastak_user_type attr, remove svc_nodered, tag accounts
  ([`c6b7f78`](https://github.com/pounde/FastTAK/commit/c6b7f782fd20c0dee0fe298cfbefb322b898a904))

- **dashboard**: Add groups dropdown to user creation form
  ([`f9d211a`](https://github.com/pounde/FastTAK/commit/f9d211a43ae5c39742a64cfdd0637d8ca0425e17))

- **service-accounts**: Enforce group rules on PATCH based on fastak_user_type
  ([`8e89e60`](https://github.com/pounde/FastTAK/commit/8e89e609b9127b157291efae409cac7ee7860b54))

- **test**: Include groups in LDAP auth test user creation
  ([`38df739`](https://github.com/pounde/FastTAK/commit/38df739d38a8297423165e453bf4aa18c8a1aaf3))

- **test**: Remove groups bootstrap check (tak_ROLE_ADMIN hidden from API)
  ([`a77a5cc`](https://github.com/pounde/FastTAK/commit/a77a5cc61c8d4842040967611362a0f33737204b))

- **test**: Remove unreachable admin group enforcement test
  ([`b683a7b`](https://github.com/pounde/FastTAK/commit/b683a7b672673853fae2fbc9c98dc8476684f814))

- **users**: Require groups on user creation, validate existence, enforce type rules
  ([`fc841bd`](https://github.com/pounde/FastTAK/commit/fc841bdfdcdc1648f49b69fb19995a6ca1c98074))

### Documentation

- Add DD-032 for fastak_user_type, create user-types reference, update cert guide
  ([`977bfe3`](https://github.com/pounde/FastTAK/commit/977bfe3c209f4c6898cf86ec0ce042042acc7bd9))

### Features

- **identity**: Add fastak_user_type attribute support to IdentityClient
  ([`1e5ee11`](https://github.com/pounde/FastTAK/commit/1e5ee111b74d656654ed427f03ef92477d334231))

### Testing

- **integration**: Add group enforcement tests, fix user creation tests
  ([`32427e4`](https://github.com/pounde/FastTAK/commit/32427e4673d95c2761c089012325366a482137ee))

- **integration**: Verify bootstrap state — expected accounts and groups
  ([`c664103`](https://github.com/pounde/FastTAK/commit/c66410384e1bb8efebb9afe77a4e59f0e2d58224))


## v0.16.0 (2026-04-10)

### Bug Fixes

- **identity**: Widen numeric ID hash to 53 bits, raise on collision
  ([`2b277f4`](https://github.com/pounde/FastTAK/commit/2b277f411414173067c7d0d560787c05abe1c315))

### Chores

- Add comment to .env.example indicating TAK_WEBADMIN_PASSWORD is optional
  ([`fb5ca51`](https://github.com/pounde/FastTAK/commit/fb5ca51e3edfd28647d1479ba63f0ccff1796c03))

### Documentation

- Update documentation for LLDAP replacement
  ([`6b44701`](https://github.com/pounde/FastTAK/commit/6b44701ff59ef8d4d507227ba01041a06a976609))

### Features

- Replace Authentik with LLDAP + ldap-proxy in Docker Compose stack
  ([`86ceadb`](https://github.com/pounde/FastTAK/commit/86ceadb42480ad10b579af51eafc5e91285dbfde))

- **init-identity**: Rewrite bootstrap from Authentik REST to LLDAP GraphQL
  ([`4640e3a`](https://github.com/pounde/FastTAK/commit/4640e3a14c6e198f8be548b05998251a16df846e))

- **ldap-proxy**: Add LDAP bind proxy with token store, REST API, and forward auth
  ([`db95f2b`](https://github.com/pounde/FastTAK/commit/db95f2b5fadf15f1a2bed910d874307db5f4dab5))

- **monitor**: Replace AuthentikClient with IdentityClient, migrate all modules
  ([`7ecdc3b`](https://github.com/pounde/FastTAK/commit/7ecdc3b79084c47045a91e6444f8a3db7f17d873))

### Refactoring

- **ldap-proxy**: Move admin creds into LDAPProxy struct, document internal API
  ([`c15ae2e`](https://github.com/pounde/FastTAK/commit/c15ae2e7b854d1a20b7eed64bf50ab4208cdb8a1))

### Testing

- Add unit tests for untested IdentityClient methods
  ([`77308cd`](https://github.com/pounde/FastTAK/commit/77308cd22bf26c99a3c181f5db0dddf0d05d55ef))

- Update integration tests for LLDAP/proxy backend, document decisions
  ([`3b632a1`](https://github.com/pounde/FastTAK/commit/3b632a1e4632f53a0c06f99230ec254928659895))


## v0.15.0 (2026-04-07)

### Bug Fixes

- Include mode in Authentik proxy provider PATCH
  ([`549b48a`](https://github.com/pounde/FastTAK/commit/549b48a9bb08a9b515bc980183ba39e201f74d3b))

- Make video-cot.sh protocol flag control actual ingest method
  ([`86dda85`](https://github.com/pounde/FastTAK/commit/86dda853de53ea66625f4ae11074cde66472a20e))

### Features

- Add custom MediaMTX config with TCP-only RTSP
  ([`04c3c35`](https://github.com/pounde/FastTAK/commit/04c3c35a636d2953fca9cce21b00e9192ed87b5a))


## v0.14.0 (2026-04-07)

### Chores

- Update video test script to default to downtown NYC
  ([`0b077f3`](https://github.com/pounde/FastTAK/commit/0b077f31505a3d13c54a5f66c8b349fa38906881))

### Features

- Add data package download for TAK client provisioning
  ([`cc6f834`](https://github.com/pounde/FastTAK/commit/cc6f834144a5abace6bf1be890b4f02bd159b874))

### Testing

- Migrate integration tests from bash to pytest (69 tests)
  ([`b59e3f8`](https://github.com/pounde/FastTAK/commit/b59e3f8cfae297096295f221204b014b3afbc105))


## v0.13.0 (2026-04-06)

### Features

- Add PgBouncer for Authentik connection pooling
  ([`37b2632`](https://github.com/pounde/FastTAK/commit/37b2632e671ca3e7237b73e8030f8769fd82aa37))


## v0.12.0 (2026-04-06)

### Bug Fixes

- Add default_sni to direct mode Caddyfile
  ([`6233fd5`](https://github.com/pounde/FastTAK/commit/6233fd56331c139c5923aad7b3205fe136121166))

- Justfile up/down recipes use shebang for DEPLOY_MODE logic
  ([`991bda8`](https://github.com/pounde/FastTAK/commit/991bda836b9145213ee215e6160bc2db124d79db))

- Postgres tuning to prevent Authentik connection exhaustion
  ([`3e942c6`](https://github.com/pounde/FastTAK/commit/3e942c6686a5ca5d43ef249a696a0845c52d5c2e))

- TLS health probe mode-aware, fix docs port table
  ([`0f05036`](https://github.com/pounde/FastTAK/commit/0f05036a7b94c5016f9a3a6e9a6eb491aae44a93))

### Chores

- Rename FQDN to SERVER_ADDRESS in portal, nodered, setup, and video-cot scripts
  ([`2318635`](https://github.com/pounde/FastTAK/commit/2318635a70f5e1e8570a2b65599e19fdf27ad40a))

### Documentation

- Add deploy modes documentation and decision record
  ([`f5b106e`](https://github.com/pounde/FastTAK/commit/f5b106e0ed2e723ef8ab9423800d60efdc09fc4f))

- Address PR review — link to cert guide, broaden TAK enrollment note
  ([`cef5177`](https://github.com/pounde/FastTAK/commit/cef51779a0183f455e440a26202a673ad37f0073))

- Update certificate guide for deploy modes
  ([`e99210b`](https://github.com/pounde/FastTAK/commit/e99210bf972fbeb85b719c72b3fa2d8c3998f01e))

### Features

- Generate Caddyfile from init-config based on DEPLOY_MODE
  ([`8819af6`](https://github.com/pounde/FastTAK/commit/8819af67d153165088b8a67e2266511cc9758c92))

- Mode-aware start.sh and justfile, remove dev-up/dev-down
  ([`b6e74dc`](https://github.com/pounde/FastTAK/commit/b6e74dc958125b5e91705d9c832d4a81a85754a1))

- **init-identity**: Create proxy providers for forward auth
  ([`33a0484`](https://github.com/pounde/FastTAK/commit/33a0484fd0134ba722447de660187b3d6c3e815a))

- **init-identity**: Mode-aware URL construction for settings.json
  ([`924b791`](https://github.com/pounde/FastTAK/commit/924b7918297aece902bfdadb8fcd5c7d24662c14))

- **monitor**: Replace Caddyfile parser with mode-aware URL builder
  ([`3fd9359`](https://github.com/pounde/FastTAK/commit/3fd935920879896a5d26e1c7b4b292fdf53ad9b3))

### Testing

- Update integration test setup for SERVER_ADDRESS rename
  ([`66b66c8`](https://github.com/pounde/FastTAK/commit/66b66c8e29e57cc9179476aca9d359c89168e960))

- Update tests for FQDN -> SERVER_ADDRESS rename
  ([`0f0a267`](https://github.com/pounde/FastTAK/commit/0f0a267db0af11c38ce1d160702aa084f082e200))


## v0.11.1 (2026-04-04)

### Bug Fixes

- Prevent LDAP/FileAuthenticator race and app-db connection exhaustion
  ([`1e14f62`](https://github.com/pounde/FastTAK/commit/1e14f621e6daceec665b5bed0e473d3b8aafea0a))


## v0.11.0 (2026-04-03)

### Bug Fixes

- Remove double quotes in confirm() that broke x-data attribute
  ([`fde46f5`](https://github.com/pounde/FastTAK/commit/fde46f510b830a233da5a32f8b1a5787d8e4d745))

### Documentation

- Add group management UI design spec
  ([`f633e48`](https://github.com/pounde/FastTAK/commit/f633e488a642b43bdc0ac75f439421fc5d7956a5))

- Add group management UI implementation plan
  ([`91fa544`](https://github.com/pounde/FastTAK/commit/91fa5441c332a011bc114e2739755a24dc8cde0e))

### Features

- Add Alpine.js state and methods for group management
  ([`bbdabb8`](https://github.com/pounde/FastTAK/commit/bbdabb8b3d87b058b3e8bc5056771cf671fe2cd8))

- Add group assignment dropdown to user detail panel
  ([`3b7aac1`](https://github.com/pounde/FastTAK/commit/3b7aac175e5aa916ec805ce11873255f93ba3cd6))

- Add groups card with create/delete on users page
  ([`b34172b`](https://github.com/pounde/FastTAK/commit/b34172bcc1b5902572756b72866fc9f8572233d8))


## v0.10.0 (2026-04-03)

### Features

- Expose TAK Server API port 8443 for external integrations
  ([`4b5619d`](https://github.com/pounde/FastTAK/commit/4b5619ddb67c9b2d06c65b1866302706a960866b))


## v0.9.0 (2026-04-01)

### Documentation

- Add Google-style API docstrings for Swagger documentation
  ([`2f19eb5`](https://github.com/pounde/FastTAK/commit/2f19eb54cf02028df8d13dfd26a941c3c19d444d))

### Features

- Filter cert health monitoring to infrastructure/service certs only
  ([#25](https://github.com/pounde/FastTAK/pull/25),
  [`2c34eda`](https://github.com/pounde/FastTAK/commit/2c34edabc49ebb0ccb880fd76bf393b4904c1a5b))


## v0.8.1 (2026-04-01)

### Bug Fixes

- **docs**: Add mkdocs extension to render mermaid diagrams
  ([`1104048`](https://github.com/pounde/FastTAK/commit/1104048a73c84ae974b4f3717edaf9fa5760ee0c))


## v0.8.0 (2026-04-01)

### Bug Fixes

- Block revoked service account cert download
  ([`3596f46`](https://github.com/pounde/FastTAK/commit/3596f467ad2104149ca9a6444dc4a53e5cd49448))

- Restore v0.7.1 version files (reverted during branch reconstruction)
  ([`c58434c`](https://github.com/pounde/FastTAK/commit/c58434cc8d27bbfc6305d86ce75054bfe38623dd))

### Documentation

- Design decisions, certificates rewrite, CLAUDE.md
  ([`dcb5dfd`](https://github.com/pounde/FastTAK/commit/dcb5dfd4b14f3c54b666cf5d6712c3ca38a68b27))

### Features

- Bootstrap cleanup and ops cert endpoint removal
  ([`0e1ac45`](https://github.com/pounde/FastTAK/commit/0e1ac45971602898e4a683a74a989a440f6bdf42))

- Service account API, user cert management, and dashboard
  ([`fde9a06`](https://github.com/pounde/FastTAK/commit/fde9a0646f70bff53d0e33197fb364ba55d62382))

- Test infrastructure — test-up/run/down, idempotent tests
  ([`39bb71e`](https://github.com/pounde/FastTAK/commit/39bb71e0f562cd8933d1ce98203c3d67d43ffc1a))

### Refactoring

- Extract shared CRL logic, add DD-028
  ([`b940ef4`](https://github.com/pounde/FastTAK/commit/b940ef4bbe4a3342a2f1995626f7e1ee8aedec79))


## v0.7.1 (2026-03-30)

### Bug Fixes

- Update dev docker compose to expose Authentik port to host
  ([`2f354a4`](https://github.com/pounde/FastTAK/commit/2f354a4a3273525bf32585af74fbb986ea36809c))


## v0.7.0 (2026-03-30)

### Bug Fixes

- Just dev-up {service} now rebuilds selected service
  ([`cbb1298`](https://github.com/pounde/FastTAK/commit/cbb1298369bd974f344986af072db68aa1dfdf27))

### Features

- **dashboard**: Add /users page route, nav link, and x-cloak CSS
  ([`0084af0`](https://github.com/pounde/FastTAK/commit/0084af0caaa3142e5fba042bbdc3849e269bf749))

- **dashboard**: Add user list with search, pagination, and full management page
  ([`0084af0`](https://github.com/pounde/FastTAK/commit/0084af0caaa3142e5fba042bbdc3849e269bf749))


## v0.6.1 (2026-03-30)

### Bug Fixes

- Correct enrollment URL
  ([`31e1d94`](https://github.com/pounde/FastTAK/commit/31e1d9435e6a88c37bf26ec404139de9ad440bc1))


## v0.6.0 (2026-03-30)

### Chores

- Block direct commits to main, exclude test override from YAML check
  ([`f4eed81`](https://github.com/pounde/FastTAK/commit/f4eed81b306982a1d1a3b53483237462e2bef77f))

### Documentation

- Add CONTRIBUTING.md and update decisions.md re: production hardening
  ([`27f8409`](https://github.com/pounde/FastTAK/commit/27f84097ebb9ec942d4239c85e95b099b626411e))

- Add DD-023 (production-first compose) and DD-024 (test port
  ([`27f8409`](https://github.com/pounde/FastTAK/commit/27f84097ebb9ec942d4239c85e95b099b626411e))

### Features

- Add dev compose override with direct-access ports
  ([`87514f6`](https://github.com/pounde/FastTAK/commit/87514f63295dcaad59852e783606522588b9af42))

- Add just recipes for production, dev, and help
  ([`27adac8`](https://github.com/pounde/FastTAK/commit/27adac82ba7b343258e46ff2305567a4106d2bcf))

- Add test compose override with +10000 port offset
  ([`3b1e715`](https://github.com/pounde/FastTAK/commit/3b1e715812ab727639eb039e6301cac6ff45bde1))

- Harden base compose — remove direct-access and admin API ports
  ([`33fec29`](https://github.com/pounde/FastTAK/commit/33fec2932256dd8acdf4fccf11f169bb0b67ed05))

- Use test compose override for port isolation in integration tests
  ([`b97fad5`](https://github.com/pounde/FastTAK/commit/b97fad55e1fc8185b3b298105831d3c863ce85f1))


## v0.5.0 (2026-03-29)

### Features

- Autovacuum tuning and direct database connection ([#9](https://github.com/pounde/FastTAK/pull/9),
  [`bdf1cac`](https://github.com/pounde/FastTAK/commit/bdf1cacec21d53c4e0f8a96df2c35313c6e724e0))

- Configurable data retention and .env additions ([#9](https://github.com/pounde/FastTAK/pull/9),
  [`bdf1cac`](https://github.com/pounde/FastTAK/commit/bdf1cacec21d53c4e0f8a96df2c35313c6e724e0))

- Data retention, autovacuum tuning, and health monitoring refactor
  ([#9](https://github.com/pounde/FastTAK/pull/9),
  [`bdf1cac`](https://github.com/pounde/FastTAK/commit/bdf1cacec21d53c4e0f8a96df2c35313c6e724e0))

- Health monitoring architecture refactor ([#9](https://github.com/pounde/FastTAK/pull/9),
  [`bdf1cac`](https://github.com/pounde/FastTAK/commit/bdf1cacec21d53c4e0f8a96df2c35313c6e724e0))


## v0.4.0 (2026-03-27)

### Chores

- Add unit tests to pre-commit hook, style fixes
  ([`3a7f3fe`](https://github.com/pounde/FastTAK/commit/3a7f3fef84f8bdee168426ce34ff0306d8779da4))

### Features

- **users**: Add user management API
  ([`59dce17`](https://github.com/pounde/FastTAK/commit/59dce175e478f3e366bae2ad9c98d99695ed4ed0))

- **users**: Isolated integration tests and setup.sh -d flag
  ([`e7877f1`](https://github.com/pounde/FastTAK/commit/e7877f1c99072ea678d17309a30efb8ddc0434f1))


## v0.3.0 (2026-03-27)

### Bug Fixes

- Add curl to monitor container for debugging and integration tests
  ([`1a15a33`](https://github.com/pounde/FastTAK/commit/1a15a33292b621459d9989a3e65a6a4fc5f0439e))

- Cert registration retry, upgrade-path generation, and review fixes
  ([`10413a4`](https://github.com/pounde/FastTAK/commit/10413a4c3e8c5d85ff10ed1f8ae530dfe583a6e3))

- Handle cd failure in register-api-cert script
  ([`0b389d5`](https://github.com/pounde/FastTAK/commit/0b389d5f0eb94c5748d9a16147ec9169a451096d))

- Improve cert registration wait loop and HTTP code parsing
  ([`a62b4f2`](https://github.com/pounde/FastTAK/commit/a62b4f2426a00ee5a3a72aaa5356575b3defb5bb))

- Prevent LDAP startup race with health check and depends_on
  ([`c3c5baf`](https://github.com/pounde/FastTAK/commit/c3c5bafd33c31ea45fd7f4dd5a23c833a418a4a0))

- Wait for cert registration before testing and add curl to monitor
  ([`58b6735`](https://github.com/pounde/FastTAK/commit/58b6735421ea8a998ad7f2d197a61e91a1e92018))

### Chores

- **ci**: Bump actions to Node.js 24 versions
  ([`acaad4a`](https://github.com/pounde/FastTAK/commit/acaad4a449f2f704d68815065ca6221a8e5acba3))

### Features

- Add svc_fasttakapi service account, rename nodered to svc_nodered
  ([`b85040d`](https://github.com/pounde/FastTAK/commit/b85040dd9a0c191d338203471156dcf4a17adabd))

- Register API service cert on TAK Server startup
  ([`719a902`](https://github.com/pounde/FastTAK/commit/719a902a319b98dad02978c09c4cbb922ecfc243))

### Refactoring

- Rename nodered cert to svc_nodered
  ([`bd2dc48`](https://github.com/pounde/FastTAK/commit/bd2dc48174bbc87bce03694864ac2ce1048b558f))

- Rename service account certs to svc_ convention
  ([`f658f2e`](https://github.com/pounde/FastTAK/commit/f658f2e9e5b202567c224823199fdbe5576c634e))

### Testing

- Add service account cert and passwordless auth integration tests
  ([`4c4653c`](https://github.com/pounde/FastTAK/commit/4c4653c8f82e9dc821e03a388e853f2de7dcf6d7))


## v0.2.0 (2026-03-25)

### Documentation

- Add Node-RED guide with USGS earthquake tutorial
  ([`adb4147`](https://github.com/pounde/FastTAK/commit/adb4147ed1aa68143da8c3c72dbda0e17f8d827a))

### Features

- **nodered**: Bake TLS config and GeoJSON-to-CoT guide into container
  ([`f887beb`](https://github.com/pounde/FastTAK/commit/f887beb90ef10de38a4807f24512ccaf11333306))


## v0.1.1 (2026-03-24)

### Bug Fixes

- Correct semantic-release config and bump upload-pages-artifact
  ([`5dc9f01`](https://github.com/pounde/FastTAK/commit/5dc9f012c5d4062f02e3516ab6fe99f31ed50614))


## v0.1.0 (2026-03-24)

- Initial Release
