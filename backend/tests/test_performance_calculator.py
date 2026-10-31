import pytest

from performance_calculator import calculate_subject_performance


def test_full_marks():
    marks = {
        "Midterm 1": 50,
        "Midterm 2": 50,
        "Internals": 20,
        "Final Exam": 100,
    }

    result = calculate_subject_performance(marks)

    assert result["total_marks"] == 100.0
    assert result["percentage"] == 100.0
    assert result["result"] == "PASS"
    assert result["missing_assessments"] == []


def test_weighted_marks_calculation():
    marks = {
        "Midterm 1": 40,
        "Midterm 2": 30,
        "Internals": 10,
        "Final Exam": 60,
    }

    result = calculate_subject_performance(marks)

    assert result["midterm_contribution"] == 22.8
    assert result["internal_contribution"] == 10.0
    assert result["final_contribution"] == 30.0
    assert result["total_marks"] == 62.8
    assert result["result"] == "PASS"


def test_swapping_midterm_marks_does_not_change_total():
    marks_a = {
        "Midterm 1": 40,
        "Midterm 2": 30,
        "Internals": 10,
        "Final Exam": 60,
    }

    marks_b = {
        "Midterm 1": 30,
        "Midterm 2": 40,
        "Internals": 10,
        "Final Exam": 60,
    }

    result_a = calculate_subject_performance(marks_a)
    result_b = calculate_subject_performance(marks_b)

    assert result_a["total_marks"] == result_b["total_marks"]
    assert result_a["midterm_contribution"] == result_b["midterm_contribution"]


def test_exact_overall_pass_boundary():
    marks = {
        "Midterm 1": 0,
        "Midterm 2": 0,
        "Internals": 0,
        "Final Exam": 80,
    }

    result = calculate_subject_performance(marks)

    assert result["total_marks"] == 40.0
    assert result["overall_passed"] is True
    assert result["final_exam_passed"] is True
    assert result["result"] == "PASS"


def test_total_below_40_fails():
    marks = {
        "Midterm 1": 20,
        "Midterm 2": 20,
        "Internals": 5,
        "Final Exam": 40,
    }

    result = calculate_subject_performance(marks)

    assert result["total_marks"] == 37.0
    assert result["overall_passed"] is False
    assert result["final_exam_passed"] is True
    assert result["result"] == "FAIL"


def test_final_exam_below_40_fails_even_if_total_passes():
    marks = {
        "Midterm 1": 50,
        "Midterm 2": 50,
        "Internals": 20,
        "Final Exam": 39,
    }

    result = calculate_subject_performance(marks)

    assert result["total_marks"] == 69.5
    assert result["overall_passed"] is True
    assert result["final_exam_passed"] is False
    assert result["result"] == "FAIL"


def test_missing_midterms_do_not_automatically_fail_if_criteria_met():
    marks = {
        "Midterm 1": None,
        "Midterm 2": None,
        "Internals": 15,
        "Final Exam": 60,
    }

    result = calculate_subject_performance(marks)

    assert result["total_marks"] == 45.0
    assert result["missing_assessments"] == ["Midterm 1", "Midterm 2"]
    assert result["missing_mandatory_assessments"] == []
    assert result["result"] == "PASS"


def test_missing_final_exam_is_zero_and_fails():
    marks = {
        "Midterm 1": 40,
        "Midterm 2": 30,
        "Internals": 10,
    }

    result = calculate_subject_performance(marks)

    assert result["assessment_marks"]["Final Exam"] == 0.0
    assert result["missing_assessments"] == ["Final Exam"]
    assert result["missing_mandatory_assessments"] == ["Final Exam"]
    assert result["result"] == "FAIL"


def test_missing_internals_fails_even_if_total_and_final_pass():
    marks = {
        "Midterm 1": 50,
        "Midterm 2": 50,
        "Internals": None,
        "Final Exam": 80,
    }

    result = calculate_subject_performance(marks)

    assert result["assessment_marks"]["Internals"] == 0.0
    assert result["missing_assessments"] == ["Internals"]
    assert result["missing_mandatory_assessments"] == ["Internals"]
    assert result["result"] == "FAIL"


def test_explicit_zero_is_not_missing():
    marks = {
        "Midterm 1": 0,
        "Midterm 2": 0,
        "Internals": 0,
        "Final Exam": 0,
    }

    result = calculate_subject_performance(marks)

    assert result["missing_assessments"] == []
    assert result["result"] == "FAIL"


@pytest.mark.parametrize(
    "assessment, score",
    [
        ("Midterm 1", -1),
        ("Midterm 1", 51),
        ("Midterm 2", -1),
        ("Midterm 2", 51),
        ("Internals", -1),
        ("Internals", 21),
        ("Final Exam", -1),
        ("Final Exam", 101),
    ],
)
def test_out_of_range_marks_raise_value_error(assessment, score):
    marks = {
        "Midterm 1": 0,
        "Midterm 2": 0,
        "Internals": 0,
        "Final Exam": 0,
    }
    marks[assessment] = score

    with pytest.raises(ValueError):
        calculate_subject_performance(marks)


@pytest.mark.parametrize(
    "invalid_score",
    [
        "abc",
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_invalid_marks_raise_value_error(invalid_score):
    marks = {
        "Midterm 1": 0,
        "Midterm 2": 0,
        "Internals": 0,
        "Final Exam": 0,
    }
    marks["Midterm 1"] = invalid_score

    with pytest.raises(ValueError):
        calculate_subject_performance(marks)


def test_non_dictionary_input_raises_value_error():
    with pytest.raises(ValueError):
        calculate_subject_performance([40, 30, 10, 60])


def test_manishs_existing_example_score():
    marks = {
        "Midterm 1": 40,
        "Midterm 2": 35,
        "Internals": 18,
        "Final Exam": 80,
    }

    result = calculate_subject_performance(marks)

    assert result["midterm_contribution"] == 23.4
    assert result["internal_contribution"] == 18.0
    assert result["final_contribution"] == 40.0
    assert result["total_marks"] == 81.4
    assert result["result"] == "PASS"