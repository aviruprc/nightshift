"""Skill vocabulary: canonical skill -> JD regex. Only skills in OWNED can ever appear on the CV; LACKED are reported as gaps."""
import re

OWNED = {
    "SQL": r"\bsql\b|t-?sql|pl/?sql|mysql|sql server",
    "Power BI": r"power ?bi|\bdax\b",
    "Tableau": r"tableau",
    "Excel (Advanced)": r"\bexcel\b|vlookup|pivot|\bvba\b",
    "SAS": r"\bsas\b",
    "QlikView": r"qlik",
    "Informatica": r"informatica|\bidmc\b|powercenter",
    "ETL Processes": r"\betl\b|data (integration|warehous)|data pipeline",
    "Autosys": r"autosys|batch (job|schedul)",
    "MySQL": r"mysql",
    "Data Governance": r"data governance|\bgovernance\b|data lineage|metadata",
    "Data Quality Management": r"data quality|\bdq\b|data (validation|cleansing|integrity)",
    "Data Reconciliation": r"reconcil",
    "Regulatory Reporting Support": r"regulatory|basel|\bcomplian|\bfinrep\b|\bcorep\b|\bbcbs\b|stress test",
    "Credit Risk Analytics": r"credit risk|risk analytics|risk (data|reporting|analyst)|\bpd\b|\blgd\b|\bifrs ?9\b|\becl\b",
    "KPI Reporting": r"\bkpi|\bmis\b|management information|\bmi reporting",
    "Dashboard Development": r"dashboard|data visuali[sz]ation|visuali[sz]ation",
    "Data Analysis & Validation": r"data analy|analytics|insight|trend analysis",
    "Stakeholder Reporting": r"stakeholder|business partner",
    "Business Requirements Gathering": r"requirement|\bbrd\b|business analys",
    "Process Improvement": r"process improvement|automation|streamlin|\bsop\b",
    "Cross-functional Collaboration": r"cross[- ]functional|collaborat",
}
LACKED = {
    "Python": r"\bpython\b|pandas|numpy", "PySpark": r"pyspark|spark", "R": r"\br programming\b|\bshiny\b",
    "Snowflake": r"snowflake", "dbt": r"\bdbt\b", "Looker": r"looker", "Azure": r"\bazure\b|synapse|data factory",
    "AWS": r"\baws\b|redshift|\bglue\b|athena", "GCP": r"\bgcp\b|bigquery", "Databricks": r"databricks",
    "Scala": r"\bscala\b", "Java": r"\bjava\b(?!script)", "Airflow": r"airflow", "Alteryx": r"alteryx",
}


def _from_facts():
    """Your own skills (facts.yaml `skills`) join OWNED as literal patterns; your `not_claimable` list joins LACKED. A built-in LACKED
    skill that YOU list as owned is removed from LACKED (a Python developer's Python is not a gap), and anything in not_claimable is
    removed from OWNED. Only skills in facts.yaml ever appear on the CV either way."""
    try:
        from core import profile
        f = profile.facts() or {}
    except Exception:  # noqa: BLE001
        return
    lit = lambda s: r"(?<!\w)" + re.escape(str(s).lower()) + r"(?!\w)"  # noqa: E731
    owned = [str(s) for v in (f.get("skills") or {}).values() for s in (v or [])]
    lacks = [str(s) for s in (f.get("not_claimable") or [])]
    for s in owned:
        OWNED.setdefault(s, lit(s))
        if s in LACKED and s not in lacks:
            LACKED.pop(s)
    for s in lacks:
        LACKED.setdefault(s, lit(s))
        OWNED.pop(s, None)


_from_facts()
OWNED_RE = {k: re.compile(v, re.I) for k, v in OWNED.items()}
LACKED_RE = {k: re.compile(v, re.I) for k, v in LACKED.items()}


def jd_terms(jd: str):
    """Returns (owned: {skill: hit_count}, lacked: [skills the JD mentions that she does not have])."""
    owned = {k: len(rx.findall(jd)) for k, rx in OWNED_RE.items()}
    owned = {k: v for k, v in owned.items() if v}
    lacked = [k for k, rx in LACKED_RE.items() if rx.search(jd)]
    return owned, lacked
