# androt_cohort.csv — provenance

**What it is.** The AndroT dataset's record of each measured application's repository: the
application id, the GitHub repository and the newest commit in AndroT's copy of it. AndroT
(Muradyan and Fazzini, MSR 2026) lists 1,337 Android projects on GitHub that are published on
Google Play or F-Droid and have tests. The cohort table (Table OA2, `Descriptive_Statistics.ipynb`)
reads `latest_commit_date` as "last commit (AndroT)".

**Built by.** `python3 tools/extract_androt_cohort.py` (2026-11-16), from AndroT's dataset file
`app_repositories_newest/androT.json`, which is not in the replication package. For each
application in `app_characteristics.csv`, it takes the one record whose `application_id` equals the
measured package id without the `.debug` suffix the debug build type adds (documenter, tipuous). It
fails when no record or more than one matches.

## What each column is

- `app`: the application's short name in the run tables.
- `application_id`: the id AndroT records, also the id the stores list.
- `repository`: the GitHub repository.
- `latest_commit_id`, `latest_commit_date`: the newest commit in AndroT's copy of the repository,
  and its date. It is not the repository's state today.

## Which commit the measured builds came from

From AndroT's commit for eight of the nine applications. linkhub's builds came from a later commit,
`6d79a778f04f9619d3e476620d19e19b7e2c329e` of 18 April 2026 (version 2.1.0, version code 46),
recorded in the thesis repository's `specs/app_inclusion.json` (not in the replication package).
