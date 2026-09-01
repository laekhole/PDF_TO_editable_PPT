"""The report must match its published schema — that is what makes it usable."""

import json
import os

import pytest

from conftest import ROOT

jsonschema = pytest.importorskip("jsonschema")

SCHEMA_PATH = os.path.join(ROOT, "docs", "report-schema.json")


@pytest.fixture(scope="module")
def validator():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@pytest.mark.parametrize(
    "name",
    [
        "shapes",
        "images",
        "text_mixed",
        "table_lattice",
        "clip_gradient",
        "dense_vector",
        "scanned",
        "rotated_pages",
        "mixed_sizes",
        "damaged_page",
    ],
)
def test_report_validates_against_the_schema(conversions, validator, name):
    report = conversions.get(name, verify=True).report
    errors = sorted(validator.iter_errors(report), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        "%s: %s" % (list(e.path), e.message) for e in errors[:5]
    )


def test_a_fallback_without_a_reason_is_rejected_by_the_schema(conversions, validator):
    """The schema is what enforces 'no silent failure'; prove it bites."""
    report = json.loads(json.dumps(conversions.get("clip_gradient", verify=True).report))
    element = next(
        e
        for page in report["pages"]
        for e in page["elements"]
        if e["outcome"] == "raster-fallback"
    )
    del element["fallbackReason"]
    assert list(validator.iter_errors(report)), "a reasonless fallback must not validate"


def test_committed_samples_validate(validator):
    import glob

    paths = glob.glob(os.path.join(ROOT, "samples", "*.report.json"))
    assert paths, "the committed samples are part of the deliverable"
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            report = json.load(fh)
        errors = list(validator.iter_errors(report))
        assert not errors, "%s: %s" % (os.path.basename(path), errors[0].message)
