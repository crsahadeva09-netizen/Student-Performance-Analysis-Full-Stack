from decimal import Decimal, InvalidOperation


ASSESSMENT_NAMES = (
    "Midterm 1",
    "Midterm 2",
    "Internals",
    "Final Exam",
)

MANDATORY_ASSESSMENTS = (
    "Internals",
    "Final Exam",
)


def calculate_subject_performance(marks_by_assessment):
    """
    Calculate one subject's result.

    Academic rules:
    - Missing Midterm 1 and/or Midterm 2: counted as 0, does NOT automatically fail.
    - Missing Internals or Final Exam: automatically FAILS the subject.
    - Passing criteria (when Internals and Final Exam are recorded):
      * Calculated total >= 40
      * Final Exam marks >= 40
    """

    if not isinstance(marks_by_assessment, dict):
        raise ValueError("Assessment marks must be provided as a dictionary.")

    marks = {}
    missing_assessments = []

    maximums = {
        "Midterm 1": Decimal("50"),
        "Midterm 2": Decimal("50"),
        "Internals": Decimal("20"),
        "Final Exam": Decimal("100"),
    }

    for name in ASSESSMENT_NAMES:
        value = marks_by_assessment.get(name)

        if value is None:
            marks[name] = Decimal("0")
            missing_assessments.append(name)
            continue

        try:
            score = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError(f"Invalid marks for {name}.")

        if not score.is_finite():
            raise ValueError(f"Invalid marks for {name}.")

        if score < 0 or score > maximums[name]:
            raise ValueError(
                f"{name} marks must be between 0 and {maximums[name]}."
            )

        marks[name] = score

    midterm_1 = marks["Midterm 1"]
    midterm_2 = marks["Midterm 2"]

    higher_midterm = max(midterm_1, midterm_2)
    lower_midterm = min(midterm_1, midterm_2)

    midterm_contribution = (
        higher_midterm * Decimal("0.48")
        + lower_midterm * Decimal("0.12")
    )

    internal_contribution = marks["Internals"]
    final_contribution = marks["Final Exam"] / Decimal("2")

    total = (
        midterm_contribution
        + internal_contribution
        + final_contribution
    )

    missing_mandatory = [
        name for name in MANDATORY_ASSESSMENTS
        if name in missing_assessments
    ]

    final_exam_passed = (
        "Final Exam" not in missing_assessments
        and marks["Final Exam"] >= Decimal("40")
    )
    overall_passed = total >= Decimal("40")
    passed = (not missing_mandatory) and overall_passed and final_exam_passed

    if passed:
        result = "PASS"
    else:
        result = "FAIL"

    return {
        "assessment_marks": {
            name: float(marks[name])
            for name in ASSESSMENT_NAMES
        },
        "missing_assessments": missing_assessments,
        "missing_mandatory_assessments": missing_mandatory,
        "midterm_contribution": float(midterm_contribution),
        "internal_contribution": float(internal_contribution),
        "final_contribution": float(final_contribution),
        "total_marks": round(float(total), 2),
        "maximum_marks": 100,
        "percentage": round(float(total), 2),
        "overall_passed": overall_passed,
        "final_exam_passed": final_exam_passed,
        "result": result,
        "grade": None,
    }