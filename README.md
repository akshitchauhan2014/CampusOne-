<div align="center">
  <img src="CampusOne-Logo.png" alt="CampusOne — graduation cap and C1 logo" width="240" />

  # CampusOne

  **A focused desktop workspace for student records and academic operations.**

  Manage student information, attendance, results, fees, and student self-service from one local application.

  <br />
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-1D4ED8?style=for-the-badge&logo=python&logoColor=white" />
  <img alt="PySide6" src="https://img.shields.io/badge/UI-PySide6-0891B2?style=for-the-badge" />
  <img alt="Database" src="https://img.shields.io/badge/Database-SQLite-172554?style=for-the-badge&logo=sqlite&logoColor=white" />
</div>

---

## Overview

CampusOne is a **local desktop application** for colleges and similar educational institutions. Built with Python, PySide6, and SQLite, it brings an administrator workspace and a student portal together in one application.

> **Designed for local use:** CampusOne is not a hosted web service. It does not provide networked multi-user access or automatic synchronization between computers.

## Features

<table>
  <tr>
    <td width="50%" valign="top">
      <h3>🎓 Student records</h3>
      Create and update student profiles, search records, preview Excel imports, review validation issues, and export reports.
    </td>
    <td width="50%" valign="top">
      <h3>📚 Courses & academics</h3>
      Maintain courses and subjects, record attendance and assessment results, and review academic summaries.
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>💳 Fee management</h3>
      Configure fee structures, record payments, view balances and collection summaries, and generate PDF payment receipts.
    </td>
    <td width="50%" valign="top">
      <h3>🧑‍🎓 Student portal</h3>
      Issue registration codes and let students sign in to view their own profile and permitted academic information.
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>📊 Analytics & early warning</h3>
      Review attendance and results dashboards, plus advisory indicators based on approved academic features.
    </td>
    <td width="50%" valign="top">
      <h3>🔐 Security & operations</h3>
      Set up administrator access, verify with passwords and recovery keys, review audit history, and create or restore verified SQLite backups.
    </td>
  </tr>
</table>

## Requirements

- **Python 3.10 or newer**
- Windows, macOS, or Linux desktop environment supported by PySide6

## Get started

### 1. Create a virtual environment

From the project directory, run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux, activate it with:

```bash
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Launch CampusOne

```bash
python run.py
```

On first launch, CampusOne initializes its database and guides the administrator through account setup. **The recovery key is displayed only once, so store it securely.**

## Typical workflow

1. Set up the administrator account and save the recovery key in a protected location.
2. Add courses and student records, or preview and import a prepared spreadsheet.
3. Record attendance, results, fee structures, and payments as needed.
4. Review dashboards, balances, audit history, and generated reports.
5. Issue registration codes to students who need portal access.
6. Create verified backups regularly and store copies separately from the computer.

## Data, privacy & responsible use

### Local data storage

CampusOne stores its database, backups, logs, and related files locally. The default database location is:

```text
data/campusone.db
```

Set `CAMPUSONE_DB_PATH` to use a different database location. Backups, exports, and imported spreadsheets may contain student personal information.

### Protect student information

- Do not commit or publish databases, backups, logs, exported reports, or spreadsheets containing student information.
- Keep these files in access-controlled locations and protect backup copies.
- The repository ignore rules exclude common local data and spreadsheet files, but review all files selected for publication before pushing.
- Assess your institution's privacy, access-control, backup, retention, and operational requirements before using CampusOne with real student data.

### Analytics are advisory

Analytics and early-warning indicators are **not predictions of fact**. Review the available input data and its limitations, have the institution approve operational thresholds, and keep decisions about students subject to appropriate human review. Do not use these indicators for automatic penalties or other adverse decisions.

## Development & tests

Install development dependencies and run the test suite:

```bash
pip install -r requirements-dev.txt
pytest -q
```

The repository also includes a GitHub Actions workflow for automated checks.

---

<div align="center">
  <sub>CampusOne · Student records and academic operations, organized in one desktop workspace.</sub>
</div>
