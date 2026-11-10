const CANDIDATE_API_URLS = [
    "http://127.0.0.1:5000",
    "http://localhost:5000",
];

let API_BASE_URL = CANDIDATE_API_URLS[0];
let apiResolved = false;

const $ = (id) => document.getElementById(id);
const VALID_VIEWS = new Set(["overview", "directory", "attendance", "students", "marks", "analytics"]);
let currentView = "overview";
let analyticsRequestId = 0;
let studentPerformanceRequestId = 0;
let studentsLoaded = false;
let studentsLoadPromise = null;
let filtersLoaded = false;
let filtersLoadPromise = null;
let branchesCache = [];
let assignmentsCache = [];
let assessmentsForSubjectId = null;
let assessmentsRequestId = 0;
let marksRequestId = 0;
let lastAnalyticsData = null;
let lastAttendanceRecords = [];
const autoSaveTimers = {};

function getSavedView() {
    try {
        const savedView = sessionStorage.getItem("studentAnalytics.currentView");
        return VALID_VIEWS.has(savedView) ? savedView : "overview";
    } catch {
        return "overview";
    }
}

function setMessage(id, message, type = "") {
    const element = $(id);
    if (!element) return;
    element.textContent = message;
    element.className = `message ${type}`;
}

async function resolveApiBaseUrl() {
    if (apiResolved) return API_BASE_URL;
    if (window.location.port === "5000") {
        API_BASE_URL = window.location.origin;
        apiResolved = true;
        return API_BASE_URL;
    }
    for (const url of CANDIDATE_API_URLS) {
        try {
            const resp = await fetch(`${url}/api/health`, { credentials: "include" });
            if (resp.ok) {
                API_BASE_URL = url;
                apiResolved = true;
                return API_BASE_URL;
            }
        } catch {
            // Try next candidate
        }
    }
    API_BASE_URL = "http://127.0.0.1:5000";
    apiResolved = true;
    return API_BASE_URL;
}

async function apiRequest(path, options = {}) {
    await resolveApiBaseUrl();

    const response = await fetch(`${API_BASE_URL}${path}`, {
        credentials: "include",
        ...options,
        headers: {
            ...(options.body ? { "Content-Type": "application/json" } : {}),
            ...options.headers,
        },
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(data.message || `Request failed (${response.status})`);
    }

    return data;
}

function showLanding() {
    $("landingPage")?.classList.remove("hidden");
    $("loginPage")?.classList.add("hidden");
    $("appPage")?.classList.add("hidden");
    window.scrollTo({ top: 0, behavior: "smooth" });
}

function showLogin() {
    $("landingPage")?.classList.add("hidden");
    $("appPage")?.classList.add("hidden");
    $("loginPage")?.classList.remove("hidden");
    setMessage("loginMessage", "");
    setTimeout(() => $("username")?.focus(), 50);
}

function showApp(username = "Administrator") {
    $("landingPage")?.classList.add("hidden");
    $("loginPage")?.classList.add("hidden");
    $("appPage")?.classList.remove("hidden");
    $("signedInUser").textContent = username;
}

// Landing Page <-> Login Page buttons
["openLoginFromNavBtn", "openLoginFromHeroBtn", "openLoginFromContactBtn"].forEach((btnId) => {
    $(btnId)?.addEventListener("click", showLogin);
});
$("backToLandingBtn")?.addEventListener("click", showLanding);

function getSelectedBranchId() {
    const subjectId = Number($("subjectId")?.value);
    if (Number.isInteger(subjectId) && subjectId > 0) {
        const assignment = assignmentsCache.find((a) => Number(a.branch_subject_id) === subjectId);
        if (assignment) return Number(assignment.branch_id);
    }
    const rawBranch = $("overviewBranch")?.value;
    return rawBranch ? Number(rawBranch) : null;
}

function showView(view) {
    currentView = VALID_VIEWS.has(view) ? view : "overview";
    view = currentView;

    try {
        sessionStorage.setItem("studentAnalytics.currentView", view);
    } catch {
        // Ignore unavailable browser storage.
    }

    $("overviewView").classList.toggle("hidden", view !== "overview");
    $("studentsView").classList.toggle("hidden", view !== "students");
    $("directoryView").classList.toggle("hidden", view !== "directory");
    $("attendanceView").classList.toggle("hidden", view !== "attendance");
    $("analyticsView").classList.toggle("hidden", view !== "analytics");
    $("marksView").classList.toggle("hidden", view !== "marks");

    const titles = {
        overview: "Performance dashboard",
        directory: "Students",
        attendance: "Attendance management",
        students: "Student performance & attendance",
        marks: "Marks entry",
        analytics: "Academic analytics"
    };

    $("pageTitle").textContent = titles[view] || titles.overview;

    document.querySelectorAll(".nav-item").forEach((button) => {
        button.classList.toggle("active", button.dataset.view === view);
    });

    loadPerformanceFilters();

    if (view === "directory") {
        loadStudents().then(() => {
            loadFilterOptions();
            renderDirectory();
            populateDeleteStudentDropdown();
        });
    }
    if (view === "attendance") {
        loadStudents().then(() => {
            loadFilterOptions();
            loadAttendance();
            loadDailyAttendanceRoster();
            loadExamAbsenceVerification();
            populateAttendanceStudentDropdown();
        });
    }
    if (view === "analytics") {
        renderAnalyticsCharts();
    }
    if (view === "students") {
        loadStudents().then(populateAllSubjectsStudentDropdown);
    }
    if (view === "marks") {
        loadStudents().then(populateMarksStudentDropdown);
        ensureMarksSubjectSelected();
        loadAssessments();
        loadExistingMarks();
    }
}

function formatNumber(value, suffix = "") {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "—";
    }
    return `${Number(value).toFixed(2)}${suffix}`;
}

function pill(result) {
    const cssClass = result === "PASS" ? "pass" :
        result === "FAIL" ? "fail" : "incomplete";
    return `<span class="result-pill ${cssClass}">${escapeHtml(result || "UNKNOWN")}</span>`;
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;",
        '"': "&quot;", "'": "&#39;",
    })[character]);
}

async function loadPerformanceFilters(force = false) {
    if (filtersLoaded && branchesCache.length > 0 && !force) {
        populateOverviewBranchDropdown();
        populateOverviewSubjectDropdown();
        populateNewStudentBranchDropdown();
        return;
    }
    if (filtersLoadPromise) return filtersLoadPromise;

    filtersLoadPromise = (async () => {
        try {
            const data = await apiRequest("/api/performance/filters");
            branchesCache = data.branches || [];
            assignmentsCache = data.assignments || [];
            filtersLoaded = branchesCache.length > 0;
        } catch (error) {
            try {
                const stData = await apiRequest("/api/students");
                const stList = stData.students || [];
                const branchMap = new Map();
                stList.forEach((s) => {
                    if (s.branch_id && !branchMap.has(s.branch_id)) {
                        branchMap.set(s.branch_id, {
                            branch_id: s.branch_id,
                            branch_code: s.branch_code,
                            branch_name: s.branch_name || s.branch_code
                        });
                    }
                });
                branchesCache = [...branchMap.values()].sort((a, b) =>
                    String(a.branch_code).localeCompare(String(b.branch_code))
                );
                filtersLoaded = branchesCache.length > 0;
            } catch {
                setMessage("dashboardMessage", error.message, "error");
            }
        } finally {
            populateOverviewBranchDropdown();
            populateOverviewSubjectDropdown();
            populateNewStudentBranchDropdown();
            filtersLoadPromise = null;
        }
    })();

    return filtersLoadPromise;
}

function populateOverviewBranchDropdown() {
    const branchSelect = $("overviewBranch");
    if (!branchSelect) return;
    const prev = branchSelect.value;

    branchSelect.innerHTML =
        '<option value="">Overall — All branches</option>' +
        branchesCache.map((b) => `
            <option value="${b.branch_id}">
                ${escapeHtml(b.branch_code)} — ${escapeHtml(b.branch_name)}
            </option>
        `).join("");

    if ([...branchSelect.options].some((o) => o.value === prev)) {
        branchSelect.value = prev;
    }
}

function populateNewStudentBranchDropdown() {
    const select = $("newStudentBranch");
    if (!select) return;
    const currentTopBranch = $("overviewBranch")?.value || "";

    select.innerHTML =
        '<option value="">Select branch</option>' +
        branchesCache.map((b) => `
            <option value="${b.branch_id}">
                ${escapeHtml(b.branch_code)} — ${escapeHtml(b.branch_name)}
            </option>
        `).join("");

    if (currentTopBranch && [...select.options].some((o) => o.value === currentTopBranch)) {
        select.value = currentTopBranch;
    }
}

function populateOverviewSubjectDropdown() {
    const branchSelect = $("overviewBranch");
    const subjectSelect = $("subjectId");
    if (!subjectSelect) return;

    const selectedBranchId = branchSelect && branchSelect.value ? Number(branchSelect.value) : null;
    const prevSubject = subjectSelect.value;

    const filteredAssignments = selectedBranchId
        ? assignmentsCache.filter((a) => Number(a.branch_id) === selectedBranchId)
        : assignmentsCache;

    const allLabel = selectedBranchId
        ? "All subjects in selected branch"
        : "Overall — All branches and subjects";

    subjectSelect.innerHTML =
        `<option value="">${escapeHtml(allLabel)}</option>` +
        filteredAssignments.map((a) => `
            <option value="${a.branch_subject_id}" data-branch-id="${a.branch_id}">
                ${escapeHtml(a.branch_code)} · Sem ${escapeHtml(a.semester)} · ${escapeHtml(a.subject_code)} — ${escapeHtml(a.subject_name)}
            </option>
        `).join("");

    if ([...subjectSelect.options].some((o) => o.value === prevSubject)) {
        subjectSelect.value = prevSubject;
    } else {
        subjectSelect.value = "";
    }
}

function ensureMarksSubjectSelected() {
    const subjectSelect = $("subjectId");
    if (!subjectSelect || subjectSelect.value) return;

    const firstRealOption = [...subjectSelect.options].find((o) => Number(o.value) > 0);
    if (firstRealOption) {
        subjectSelect.value = firstRealOption.value;
    }
}

function renderPieChart(passed = 0, failed = 0) {
    const chart = $("overviewPieChart");
    const centerPass = $("pieCenterPass");
    const legendPass = $("pieLegendPass");
    const legendFail = $("pieLegendFail");

    if (!chart) return;

    const passCount = Number(passed) || 0;
    const failCount = Number(failed) || 0;
    const denom = passCount + failCount;

    if (denom <= 0) {
        chart.style.background = "conic-gradient(#e4e9f2 0deg 360deg)";
        if (centerPass) centerPass.textContent = "—";
        if (legendPass) legendPass.textContent = "0 students (0.00%)";
        if (legendFail) legendFail.textContent = "0 students (0.00%)";
        return;
    }

    const passPct = (passCount / denom) * 100;
    const failPct = (failCount / denom) * 100;
    const passDeg = (passPct / 100) * 360;

    chart.style.background = `conic-gradient(#15845b 0deg ${passDeg}deg, #c64242 ${passDeg}deg 360deg)`;

    if (centerPass) centerPass.textContent = `${passPct.toFixed(1)}%`;
    if (legendPass) legendPass.textContent = `${passCount} student(s) (${passPct.toFixed(2)}%)`;
    if (legendFail) legendFail.textContent = `${failCount} student(s) (${failPct.toFixed(2)}%)`;
}


// ==========================================================
// UNIVERSAL STUDENT TABLE PAGINATION ENGINE (10, 25, 50, ALL)
// ==========================================================
const tablePaginationState = {
    topPerformersBody: { pageSize: "10", currentPage: 1 },
    supportBody: { pageSize: "10", currentPage: 1 },
    directoryBody: { pageSize: "10", currentPage: 1 },
    dailyRosterBody: { pageSize: "10", currentPage: 1 },
    examVerificationBody: { pageSize: "10", currentPage: 1 },
    attendanceBody: { pageSize: "10", currentPage: 1 },
    analyticsDiagnosticBody: { pageSize: "10", currentPage: 1 },
};

function paginateRows(tbodyId, allRows, onPageChange) {
    if (!tablePaginationState[tbodyId]) {
        tablePaginationState[tbodyId] = { pageSize: "10", currentPage: 1 };
    }
    const state = tablePaginationState[tbodyId];
    const total = Array.isArray(allRows) ? allRows.length : 0;
    const isAll = state.pageSize === "all";
    const size = isAll ? (total || 1) : Number(state.pageSize) || 10;
    const totalPages = Math.max(1, Math.ceil(total / size));

    if (state.currentPage > totalPages) state.currentPage = totalPages;
    if (state.currentPage < 1) state.currentPage = 1;

    const startIdx = isAll ? 0 : (state.currentPage - 1) * size;
    const endIdx = isAll ? total : Math.min(startIdx + size, total);
    const pageRows = isAll ? allRows : allRows.slice(startIdx, endIdx);

    renderPaginationBar(tbodyId, total, startIdx, endIdx, state.currentPage, totalPages, state.pageSize, onPageChange);
    return pageRows;
}

function resetTablePage(tbodyId) {
    if (tablePaginationState[tbodyId]) {
        tablePaginationState[tbodyId].currentPage = 1;
    }
}

function renderPaginationBar(tbodyId, total, startIdx, endIdx, currentPage, totalPages, pageSize, onPageChange) {
    const tbody = $(tbodyId);
    if (!tbody) return;
    const tableWrap = tbody.closest(".table-wrap");
    if (!tableWrap) return;

    // Remove any max-height scroll trap so the pagination bar cleanly controls visible rows
    tableWrap.style.maxHeight = "none";

    const barId = `${tbodyId}_paginationBar`;
    let bar = $(barId);
    if (!bar) {
        bar = document.createElement("div");
        bar.id = barId;
        bar.className = "table-pagination-bar";
        tableWrap.insertAdjacentElement("afterend", bar);
    }

    const fromNum = total === 0 ? 0 : startIdx + 1;
    const toNum = total === 0 ? 0 : endIdx;

    bar.innerHTML = `
        <div class="pagination-info">
            Showing <strong>${fromNum}–${toNum}</strong> of <strong>${total}</strong> student(s)
        </div>
        <div class="pagination-controls">
            <label class="page-size-label">
                Show students:
                <select class="page-size-select" data-tbody-id="${tbodyId}">
                    <option value="10" ${String(pageSize) === "10" ? "selected" : ""}>10</option>
                    <option value="25" ${String(pageSize) === "25" ? "selected" : ""}>25</option>
                    <option value="50" ${String(pageSize) === "50" ? "selected" : ""}>50</option>
                    <option value="all" ${String(pageSize) === "all" ? "selected" : ""}>All</option>
                </select>
            </label>
            <div class="page-nav-group">
                <button type="button" class="secondary-button page-nav-btn prev-page-btn" ${currentPage <= 1 ? "disabled" : ""}>
                    ← Prev
                </button>
                <span class="page-indicator">Page ${currentPage} of ${totalPages}</span>
                <button type="button" class="secondary-button page-nav-btn next-page-btn" ${currentPage >= totalPages ? "disabled" : ""}>
                    Next →
                </button>
            </div>
        </div>
    `;

    const selectEl = bar.querySelector(".page-size-select");
    selectEl?.addEventListener("change", (e) => {
        tablePaginationState[tbodyId].pageSize = e.target.value;
        tablePaginationState[tbodyId].currentPage = 1;
        if (typeof onPageChange === "function") onPageChange();
    });

    const prevBtn = bar.querySelector(".prev-page-btn");
    prevBtn?.addEventListener("click", () => {
        if (tablePaginationState[tbodyId].currentPage > 1) {
            tablePaginationState[tbodyId].currentPage--;
            if (typeof onPageChange === "function") onPageChange();
        }
    });

    const nextBtn = bar.querySelector(".next-page-btn");
    nextBtn?.addEventListener("click", () => {
        if (tablePaginationState[tbodyId].currentPage < totalPages) {
            tablePaginationState[tbodyId].currentPage++;
            if (typeof onPageChange === "function") onPageChange();
        }
    });
}

let cachedTopPerformersList = [];
let cachedSupportList = [];

function renderTopPerformers(students = null) {
    const body = $("topPerformersBody");
    if (!body) return;

    if (students !== null) {
        const allPassing = (lastAnalyticsData?.all_students || []).filter((s) => s.result === "PASS");
        cachedTopPerformersList = allPassing.length >= students.length ? allPassing : students;
    }

    const pageRows = paginateRows("topPerformersBody", cachedTopPerformersList, () => renderTopPerformers(null));

    if (!cachedTopPerformersList.length) {
        body.innerHTML = '<tr><td colspan="5" class="empty-cell">No passing student records yet.</td></tr>';
        return;
    }

    body.innerHTML = pageRows.map((student) => `
        <tr>
            <td>#${escapeHtml(student.rank)}</td>
            <td><strong>${escapeHtml(student.student_name)}</strong></td>
            <td>${escapeHtml(student.roll_no)}</td>
            <td>${formatNumber(student.total_marks)}</td>
            <td>${pill(student.result)}</td>
        </tr>
    `).join("");
}

function renderSupport(students = null) {
    const body = $("supportBody");
    if (!body) return;

    if (students !== null) {
        cachedSupportList = students;
    }

    const pageRows = paginateRows("supportBody", cachedSupportList, () => renderSupport(null));

    if (!cachedSupportList.length) {
        body.innerHTML = '<tr><td colspan="4" class="empty-cell">No students currently need support.</td></tr>';
        return;
    }

    body.innerHTML = pageRows.map((student) => `
        <tr>
            <td><strong>${escapeHtml(student.student_name)}</strong></td>
            <td>${escapeHtml(student.roll_no)}</td>
            <td class="reason-cell">${escapeHtml(student.failure_explanation || "Needs support")}</td>
            <td>${pill(student.result)}</td>
        </tr>
    `).join("");
}

async function loadAnalytics() {
    await loadPerformanceFilters();

    const rawSubjectId = $("subjectId").value.trim();
    const rawBranchId = $("overviewBranch") ? $("overviewBranch").value.trim() : "";

    const subjectId = rawSubjectId ? Number(rawSubjectId) : null;
    const branchId = rawBranchId ? Number(rawBranchId) : null;

    const requestId = ++analyticsRequestId;
    $("loadSubjectButton").disabled = true;
    setMessage("dashboardMessage", "Loading current analytics…");

    try {
        let data;

        if (subjectId && Number.isInteger(subjectId) && subjectId > 0) {
            data = await apiRequest(`/api/performance/subjects/${subjectId}/analytics`);
            if (requestId !== analyticsRequestId) return;

            $("subjectLabel").textContent =
                `${data.subject.branch_code} · Sem ${data.subject.semester} · ${data.subject.subject_code} — ${data.subject.subject_name}`;
        } else {
            const query = branchId ? `?branch_id=${branchId}` : "";
            data = await apiRequest(`/api/performance/overview${query}`);
            if (requestId !== analyticsRequestId) return;

            $("subjectLabel").textContent =
                data.scope?.label || "Overall — All branches and subjects";
        }

        const analytics = data.analytics;
        lastAnalyticsData = analytics;

        $("totalStudents").textContent = analytics.total_students ?? "—";
        $("classAverage").textContent = formatNumber(analytics.class_average);
        $("passPercentage").textContent = formatNumber(analytics.pass_percentage, "%");
        $("supportCount").textContent = analytics.students_needing_support?.length ?? 0;
        $("passedStudents").textContent = analytics.passed_students ?? 0;
        $("failedStudents").textContent = analytics.failed_students ?? 0;
        $("incompleteStudents").textContent = analytics.incomplete_students ?? 0;

        renderPieChart(
            analytics.passed_students,
            analytics.failed_students
        );
        resetTablePage("topPerformersBody");
        resetTablePage("supportBody");
        resetTablePage("analyticsDiagnosticBody");
        renderTopPerformers(analytics.top_performers || []);
        renderSupport(analytics.students_needing_support || []);
        renderAnalyticsCharts();

        setMessage("dashboardMessage", "Analytics refreshed successfully.", "success");
    } catch (error) {
        if (requestId !== analyticsRequestId) return;
        setMessage("dashboardMessage", error.message, "error");
        if (/log in|unauthorized|session/i.test(error.message)) showLogin();
    } finally {
        if (requestId === analyticsRequestId) $("loadSubjectButton").disabled = false;
    }
}

// Student Performance Tab: All-subjects breakdown + Subject-wise Bar Graph + Distinct Subject Attendance Percentages below
async function loadStudentAllSubjectsPerformance() {
    const studentId = Number($("allSubjectsStudentSelect")?.value);
    const resultDiv = $("studentAllSubjectsResult");

    if (!Number.isInteger(studentId) || studentId <= 0) {
        resultDiv.innerHTML = "Please select a student from the dropdown above.";
        return;
    }

    const requestId = ++studentPerformanceRequestId;
    resultDiv.innerHTML = "Loading student analytics across all subjects…";

    try {
        const [perfData, attData] = await Promise.all([
            apiRequest(`/api/performance/students/${studentId}/all-subjects`),
            apiRequest(`/api/attendance/student/${studentId}`).catch(() => null)
        ]);
        if (requestId !== studentPerformanceRequestId) return;

        const student = perfData.student;
        const subs = perfData.subjects_performance || [];
        const attSummary = attData?.summary || {
            total_subjects: subs.length,
            recorded_subjects: 0,
            attended_periods: 0,
            total_periods: 0,
            attendance_percentage: null,
            status: null
        };
        const subjAttList = attData?.subjects_attendance || [];
        const attBySubjectId = {};
        subjAttList.forEach((sa) => {
            attBySubjectId[Number(sa.branch_subject_id)] = sa;
        });

        let totalScoreSum = 0;
        let failedCount = 0;
        subs.forEach((s) => {
            totalScoreSum += (s.performance?.total_marks || 0);
            if (s.performance?.result !== "PASS") failedCount++;
        });
        const overallAvg = subs.length ? (totalScoreSum / subs.length) : 0;
        const overallStatus = subs.length ? (failedCount === 0 ? "PASS" : "FAIL") : "INCOMPLETE";

        const overallAttPct = attSummary.attendance_percentage != null ? Number(attSummary.attendance_percentage) : null;

        resultDiv.innerHTML = `
            <div class="student-summary-header">
                <div>
                    <h3>${escapeHtml(student.name)} · ${escapeHtml(student.roll_no)} (Semester ${escapeHtml(student.semester)})</h3>
                    <p style="margin: 4px 0 0;">
                        <strong>Overall Average Marks:</strong> ${formatNumber(overallAvg)} / 100
                        &nbsp;·&nbsp;
                        <strong>Overall Result:</strong> ${pill(overallStatus)}
                        &nbsp;·&nbsp;
                        <strong>Overall Attendance:</strong> ${overallAttPct == null ? "Not recorded" : `${overallAttPct.toFixed(1)}% (${attSummary.attended_periods || 0}/${attSummary.total_periods || 0} periods)`}
                        ${attSummary.status ? statusBadge(attSummary.status) : ""}
                    </p>
                </div>
            </div>

            <h4 style="margin: 20px 0 10px;">1. Analytics Across All Assigned Subjects</h4>
            <div class="table-wrap" style="background: #fff; border: 1px solid var(--border); border-radius: 10px;">
                <table>
                    <thead>
                        <tr>
                            <th>Subject Code</th>
                            <th>Subject Name</th>
                            <th>Midterm 1 (50)</th>
                            <th>Midterm 2 (50)</th>
                            <th>Internals (20)</th>
                            <th>Final Exam (100)</th>
                            <th>Calculated Total</th>
                            <th>Periods (Attended / Total)</th>
                            <th>Attendance %</th>
                            <th>Result</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${subs.length ? subs.map((s) => {
                            const p = s.performance || {};
                            const marks = p.assessment_marks || {};
                            const missing = new Set(p.missing_assessments || []);
                            const fmtMark = (name) => missing.has(name)
                                ? '<span class="muted">Missed (0)</span>'
                                : formatNumber(marks[name]);
                            const sa = attBySubjectId[Number(s.branch_subject_id)];
                            const periodsText = sa && sa.attended_periods != null && sa.total_periods != null
                                ? `${escapeHtml(sa.attended_periods)} / ${escapeHtml(sa.total_periods)}`
                                : '<span class="muted">—</span>';
                            const sAttText = sa && sa.attendance_percentage != null
                                ? `<strong>${Number(sa.attendance_percentage).toFixed(1)}%</strong>`
                                : '<span class="muted">—</span>';
                            return `
                                <tr>
                                    <td><strong>${escapeHtml(s.subject_code)}</strong></td>
                                    <td>${escapeHtml(s.subject_name)}</td>
                                    <td>${fmtMark("Midterm 1")}</td>
                                    <td>${fmtMark("Midterm 2")}</td>
                                    <td>${fmtMark("Internals")}</td>
                                    <td>${fmtMark("Final Exam")}</td>
                                    <td><strong>${formatNumber(p.total_marks)}</strong></td>
                                    <td>${periodsText}</td>
                                    <td>${sAttText}</td>
                                    <td>${pill(p.result)}</td>
                                </tr>
                            `;
                        }).join("") : '<tr><td colspan="10" class="empty-cell">No subjects assigned for this student.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <h4 style="margin: 26px 0 12px;">2. Bar Graph — All Subjects Performance (Marks)</h4>
            <div class="panel" style="padding: 18px 20px; background: #fff;">
                <div class="bar-list">
                    ${subs.map((s) => {
                        const mVal = Number(s.performance?.total_marks) || 0;
                        const barColor = s.performance?.result === "PASS" ? "active" : "inactive";
                        return `
                            <div class="bar-row" style="grid-template-columns: 180px minmax(140px, 3fr) 120px;">
                                <span title="${escapeHtml(s.subject_code)} — ${escapeHtml(s.subject_name)}">
                                    <strong>${escapeHtml(s.subject_code)}</strong> · ${escapeHtml(s.subject_name)}
                                </span>
                                <div class="distribution-track">
                                    <span class="distribution-fill ${barColor}" style="width:${Math.max(0, Math.min(100, mVal))}%"></span>
                                </div>
                                <strong style="text-align: right;">${mVal.toFixed(2)}% ${pill(s.performance?.result)}</strong>
                            </div>
                        `;
                    }).join("")}
                </div>
            </div>

            <h4 style="margin: 26px 0 12px;">3. Bar Graph — Respective Subject-wise Attendance Percentages</h4>
            <div class="panel" style="padding: 18px 20px; background: #fff;">
                <p style="margin: 0 0 14px; font-size: 13px;">
                    <strong>Subjects with Recorded Attendance:</strong> ${escapeHtml(attSummary.recorded_subjects)} / ${escapeHtml(attSummary.total_subjects)}
                    &nbsp;·&nbsp;
                    <strong>Total Periods:</strong> ${escapeHtml(attSummary.attended_periods || 0)} / ${escapeHtml(attSummary.total_periods || 0)}
                    &nbsp;·&nbsp;
                    <strong>Average Attendance:</strong> ${overallAttPct == null ? "Not recorded" : `${overallAttPct.toFixed(1)}%`}
                </p>
                <div class="bar-list">
                    ${subs.map((s) => {
                        const sa = attBySubjectId[Number(s.branch_subject_id)];
                        const pctVal = sa && sa.attendance_percentage != null ? Number(sa.attendance_percentage) : null;
                        const attClass = pctVal == null ? "" : (pctVal > 75 ? "active" : pctVal >= 65 ? "warning" : "inactive");
                        const pInfo = sa && sa.attended_periods != null ? `(${sa.attended_periods}/${sa.total_periods})` : "";
                        return `
                            <div class="bar-row" style="grid-template-columns: 180px minmax(140px, 3fr) 235px;">
                                <span title="${escapeHtml(s.subject_code)} — ${escapeHtml(s.subject_name)}">
                                    <strong>${escapeHtml(s.subject_code)}</strong> · ${escapeHtml(s.subject_name)}
                                </span>
                                <div class="distribution-track">
                                    <span class="distribution-fill ${attClass}" style="width:${Math.max(0, Math.min(100, pctVal ?? 0))}%"></span>
                                </div>
                                <strong style="text-align: right;">
                                    ${pctVal == null ? '<span class="muted">Not recorded</span>' : `${pInfo} ${pctVal.toFixed(1)}% ${statusBadge(sa.status)}`}
                                </strong>
                            </div>
                        `;
                    }).join("")}
                </div>
            </div>
        `;
    } catch (error) {
        if (requestId === studentPerformanceRequestId) {
            resultDiv.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
        }
    }
}

$("loadStudentAllSubjectsBtn")?.addEventListener("click", loadStudentAllSubjectsPerformance);
$("allSubjectsStudentSelect")?.addEventListener("change", () => {
    if ($("allSubjectsStudentSelect").value) {
        loadStudentAllSubjectsPerformance();
    }
});

$("loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    setMessage("loginMessage", "Signing in…");

    try {
        const data = await apiRequest("/api/auth/login", {
            method: "POST",
            body: JSON.stringify({
                username: $("username").value.trim(),
                password: $("password").value,
            }),
        });

        showApp(data.user.username);
        $("password").value = "";
        await loadPerformanceFilters();
        showView(getSavedView());
        await loadAnalytics();
    } catch (error) {
        setMessage("loginMessage", error.message, "error");
    }
});

$("logoutButton").addEventListener("click", async () => {
    try {
        await apiRequest("/api/auth/logout", { method: "POST" });
    } finally {
        showView("overview");
        try {
            sessionStorage.removeItem("studentAnalytics.currentView");
        } catch {
            // Ignore unavailable browser storage.
        }
        showLanding();
        delete $("marksForm").dataset.markId;
        $("marksMode").value = "auto";
    }
});

document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
});

$("loadSubjectButton").addEventListener("click", loadAnalytics);
$("refreshButton").addEventListener("click", () => {
    loadAnalytics();
    if (currentView === "directory") {
        studentsLoaded = false;
        loadStudents().then(() => {
            renderDirectory();
            populateDeleteStudentDropdown();
        });
    }
    if (currentView === "attendance") {
        loadAttendance();
        loadDailyAttendanceRoster();
        loadExamAbsenceVerification();
        if (!$("examSchedulePanel")?.classList.contains("hidden")) loadBranchExamSchedule();
    }
    if (currentView === "students" && $("allSubjectsStudentSelect")?.value) {
        loadStudentAllSubjectsPerformance();
    }
});

let studentsCache = [];
let assessmentsCache = [];
let existingMarksCache = [];

async function loadStudents() {
    if (studentsLoaded) {
        populateMarksStudentDropdown();
        populateAllSubjectsStudentDropdown();
        populateAttendanceStudentDropdown();
        populateDeleteStudentDropdown();
        return;
    }
    if (studentsLoadPromise) return studentsLoadPromise;

    studentsLoadPromise = (async () => {
        try {
            const data = await apiRequest("/api/students");
            studentsCache = data.students || [];
            studentsLoaded = true;
            populateMarksStudentDropdown();
            populateAllSubjectsStudentDropdown();
            populateAttendanceStudentDropdown();
            populateDeleteStudentDropdown();
        } catch (error) {
            setMessage("marksMessage", error.message, "error");
        } finally {
            studentsLoadPromise = null;
        }
    })();

    return studentsLoadPromise;
}

function getBranchFilteredStudents() {
    const selectedBranchId = getSelectedBranchId();
    return selectedBranchId
        ? studentsCache.filter((s) => Number(s.branch_id) === selectedBranchId)
        : studentsCache;
}

function populateMarksStudentDropdown() {
    const select = $("marksStudent");
    if (!select) return;

    const prevStudent = select.value;
    const filtered = getBranchFilteredStudents();

    if (!filtered.length) {
        select.innerHTML = '<option value="">No students in selected branch</option>';
        return;
    }

    select.innerHTML = '<option value="">Select a student</option>' +
        filtered.map((student) => `
            <option value="${student.student_id}">
                ${escapeHtml(student.roll_no)} — ${escapeHtml(student.name)}
                (${escapeHtml(student.branch_code)} · Sem ${escapeHtml(student.semester)})
            </option>
        `).join("");

    if ([...select.options].some((o) => o.value === prevStudent)) {
        select.value = prevStudent;
    }
}

function populateAllSubjectsStudentDropdown() {
    const select = $("allSubjectsStudentSelect");
    if (!select) return;
    const prev = select.value;
    const filtered = getBranchFilteredStudents();

    select.innerHTML = '<option value="">Select a student</option>' +
        filtered.map((s) => `
            <option value="${s.student_id}">
                ${escapeHtml(s.roll_no)} — ${escapeHtml(s.name)} (${escapeHtml(s.branch_code)} · Sem ${escapeHtml(s.semester)})
            </option>
        `).join("");

    if ([...select.options].some((o) => o.value === prev)) {
        select.value = prev;
    }
}

function populateAttendanceStudentDropdown() {
    const select = $("attStudentSelect");
    if (!select) return;
    const prev = select.value;
    const filtered = getBranchFilteredStudents();

    select.innerHTML = '<option value="">Select a student</option>' +
        filtered.map((s) => `
            <option value="${s.student_id}">
                ${escapeHtml(s.roll_no)} — ${escapeHtml(s.name)} (${escapeHtml(s.branch_code)})
            </option>
        `).join("");

    if ([...select.options].some((o) => o.value === prev)) {
        select.value = prev;
    }
}

function populateDeleteStudentDropdown() {
    const select = $("deleteStudentSelect");
    if (!select) return;
    const prev = select.value;
    const filtered = getBranchFilteredStudents();

    select.innerHTML = '<option value="">Select student to delete</option>' +
        filtered.map((s) => `
            <option value="${s.student_id}">
                ${escapeHtml(s.roll_no)} — ${escapeHtml(s.name)} (${escapeHtml(s.branch_code)})
            </option>
        `).join("");

    if ([...select.options].some((o) => o.value === prev)) {
        select.value = prev;
    }
}

// Toggle Add / Update Attendance Window (Visible only when clicked)
$("toggleAddAttendanceBtn")?.addEventListener("click", () => {
    const panel = $("attendanceFormPanel");
    if (!panel) return;
    populateAttendanceStudentDropdown();
    panel.classList.toggle("hidden");
    if (!panel.classList.contains("hidden")) {
        const sid = Number($("attStudentSelect")?.value);
        if (sid > 0) {
            loadStudentSubjectsForAttendanceForm(sid);
        }
    }
});

$("closeAttendanceFormBtn")?.addEventListener("click", () => {
    $("attendanceFormPanel")?.classList.add("hidden");
});

$("cancelAttendanceBtn")?.addEventListener("click", () => {
    $("attendanceFormPanel")?.classList.add("hidden");
});

async function autoSaveSubjectRow(studentId, bsId, attendedInput, totalInput, pctDisplay, saveStateEl) {
    const attRaw = attendedInput.value.trim();
    const totRaw = totalInput.value.trim();

    if (attRaw === "" || totRaw === "") {
        pctDisplay.textContent = "—";
        if (saveStateEl) saveStateEl.textContent = "Enter periods";
        return;
    }

    const att = Number(attRaw);
    const tot = Number(totRaw);

    if (!Number.isInteger(tot) || tot <= 0 || tot > 500) {
        pctDisplay.textContent = "Invalid total";
        if (saveStateEl) {
            saveStateEl.textContent = "Total must be 1–500";
            saveStateEl.className = "auto-save-state error-text";
        }
        return;
    }

    if (!Number.isInteger(att) || att < 0 || att > tot) {
        pctDisplay.textContent = "Invalid";
        if (saveStateEl) {
            saveStateEl.textContent = `Max ${tot}`;
            saveStateEl.className = "auto-save-state error-text";
        }
        return;
    }

    const pct = ((att * 100.0) / tot).toFixed(2);
    const stLabel = attendanceStatus(Number(pct));
    pctDisplay.innerHTML = `<strong>${pct}%</strong> ${statusBadge(stLabel)}`;

    if (saveStateEl) {
        saveStateEl.textContent = "Saving…";
        saveStateEl.className = "auto-save-state muted";
    }

    const timerKey = `${studentId}_${bsId}`;
    clearTimeout(autoSaveTimers[timerKey]);

    autoSaveTimers[timerKey] = setTimeout(async () => {
        try {
            const res = await apiRequest("/api/attendance", {
                method: "POST",
                body: JSON.stringify({
                    student_id: studentId,
                    branch_subject_id: bsId,
                    attended_periods: att,
                    total_periods: tot
                })
            });
            if (saveStateEl) {
                saveStateEl.textContent = "✓ Saved";
                saveStateEl.className = "auto-save-state success-text";
            }
            setMessage("attRecordMessage", res.message || "Auto-saved successfully.", "success");
            loadAttendance();
            if ($("allSubjectsStudentSelect") && Number($("allSubjectsStudentSelect").value) === studentId) {
                loadStudentAllSubjectsPerformance();
            }
        } catch (error) {
            if (saveStateEl) {
                saveStateEl.textContent = "Save failed";
                saveStateEl.className = "auto-save-state error-text";
            }
            setMessage("attRecordMessage", error.message, "error");
        }
    }, 350);
}

async function loadStudentSubjectsForAttendanceForm(studentId) {
    const container = $("attSubjectsInputsContainer");
    if (!container) return;

    if (!Number.isInteger(studentId) || studentId <= 0) {
        container.innerHTML = '<p class="muted">Select a student above to load all their subjects.</p>';
        return;
    }

    container.innerHTML = '<p class="muted">Loading student subjects…</p>';

    try {
        const data = await apiRequest(`/api/attendance/student/${studentId}`);
        const subjects = data.subjects_attendance || [];

        if (!subjects.length) {
            container.innerHTML = "<p class=\"muted\">No subjects assigned for this student's branch and semester.</p>";
            return;
        }

        container.innerHTML = `
            <div class="table-wrap" style="background: #fff; border: 1px solid #d8dfeb; border-radius: 10px;">
                <table class="subject-periods-table">
                    <thead>
                        <tr>
                            <th>Subject</th>
                            <th>Periods Attended</th>
                            <th>Total Periods</th>
                            <th>Calculated Percentage</th>
                            <th>Auto-save Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${subjects.map((sub) => {
                            const bsId = sub.branch_subject_id;
                            const attVal = sub.attended_periods != null ? sub.attended_periods : "";
                            const totVal = sub.total_periods != null ? sub.total_periods : 50;
                            const pctVal = sub.attendance_percentage != null
                                ? `<strong>${Number(sub.attendance_percentage).toFixed(2)}%</strong> ${statusBadge(sub.status)}`
                                : '<span class="muted">—</span>';
                            return `
                                <tr class="subject-period-row" data-branch-subject-id="${bsId}">
                                    <td>
                                        <strong>${escapeHtml(sub.subject_code)}</strong> — ${escapeHtml(sub.subject_name)}
                                    </td>
                                    <td>
                                        <input
                                            id="attPeriods_${bsId}"
                                            class="period-input attended-input"
                                            type="number"
                                            min="0"
                                            max="500"
                                            step="1"
                                            placeholder="Attended"
                                            value="${attVal}"
                                            required
                                        >
                                    </td>
                                    <td>
                                        <input
                                            id="totPeriods_${bsId}"
                                            class="period-input total-input"
                                            type="number"
                                            min="1"
                                            max="500"
                                            step="1"
                                            placeholder="Total"
                                            value="${totVal}"
                                            required
                                        >
                                    </td>
                                    <td id="calcPct_${bsId}" class="calc-pct-cell">${pctVal}</td>
                                    <td>
                                        <span id="saveState_${bsId}" class="auto-save-state muted">
                                            ${sub.attendance_percentage != null ? "✓ Saved" : "Ready"}
                                        </span>
                                    </td>
                                </tr>
                            `;
                        }).join("")}
                    </tbody>
                </table>
            </div>
        `;

        container.querySelectorAll(".subject-period-row").forEach((row) => {
            const bsId = Number(row.dataset.branchSubjectId);
            const attInput = row.querySelector(".attended-input");
            const totInput = row.querySelector(".total-input");
            const pctCell = $(`calcPct_${bsId}`);
            const saveState = $(`saveState_${bsId}`);

            const triggerUpdate = () => {
                autoSaveSubjectRow(studentId, bsId, attInput, totInput, pctCell, saveState);
            };

            attInput.addEventListener("input", triggerUpdate);
            totInput.addEventListener("input", triggerUpdate);
        });
    } catch (error) {
        container.innerHTML = `<p class="message error">${escapeHtml(error.message)}</p>`;
    }
}

$("attStudentSelect")?.addEventListener("change", () => {
    const sid = Number($("attStudentSelect")?.value);
    loadStudentSubjectsForAttendanceForm(sid);
});

// Explicit Save All button (also saves all rows immediately)
$("attendanceRecordForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const studentId = Number($("attStudentSelect")?.value);
    if (!studentId) {
        setMessage("attRecordMessage", "Please select a student first.", "error");
        return;
    }

    const rows = [...document.querySelectorAll(".subject-period-row")];
    if (!rows.length) {
        setMessage("attRecordMessage", "No subjects loaded for this student.", "error");
        return;
    }

    const subjectRecords = [];
    for (const row of rows) {
        const bsId = Number(row.dataset.branchSubjectId);
        const attInput = row.querySelector(".attended-input");
        const totInput = row.querySelector(".total-input");
        const att = Number(attInput.value.trim());
        const tot = Number(totInput.value.trim());

        if (!Number.isInteger(tot) || tot <= 0 || !Number.isInteger(att) || att < 0 || att > tot) {
            setMessage("attRecordMessage", "Enter valid Periods Attended (0 to Total) and Total Periods (> 0) for each subject.", "error");
            attInput.focus();
            return;
        }
        subjectRecords.push({
            branch_subject_id: bsId,
            attended_periods: att,
            total_periods: tot
        });
    }

    $("saveAttendanceBtn").disabled = true;
    setMessage("attRecordMessage", "Saving all subject attendance records...");

    try {
        const res = await apiRequest("/api/attendance", {
            method: "POST",
            body: JSON.stringify({
                student_id: studentId,
                subject_records: subjectRecords
            })
        });
        rows.forEach((row) => {
            const bsId = Number(row.dataset.branchSubjectId);
            const saveState = $(`saveState_${bsId}`);
            if (saveState) {
                saveState.textContent = "✓ Saved";
                saveState.className = "auto-save-state success-text";
            }
        });
        setMessage("attRecordMessage", res.message || "Subject attendance saved successfully.", "success");
        await loadAttendance();
        if ($("allSubjectsStudentSelect") && Number($("allSubjectsStudentSelect").value) === studentId) {
            loadStudentAllSubjectsPerformance();
        }
    } catch (error) {
        setMessage("attRecordMessage", error.message, "error");
    } finally {
        $("saveAttendanceBtn").disabled = false;
    }
});

async function loadAssessments() {
    ensureMarksSubjectSelected();
    const subjectId = Number($("subjectId").value);
    const select = $("marksAssessment");

    if (!Number.isInteger(subjectId) || subjectId <= 0) {
        assessmentsForSubjectId = null;
        assessmentsCache = [];
        select.innerHTML = '<option value="">Select a specific subject in the top bar first</option>';
        updateMarksMaximum();
        return;
    }

    const requestId = ++assessmentsRequestId;
    assessmentsForSubjectId = null;
    select.innerHTML = '<option value="">Loading assessments...</option>';

    try {
        const data = await apiRequest(
            `/api/assessments?branch_subject_id=${subjectId}`
        );
        if (requestId !== assessmentsRequestId || Number($("subjectId").value) !== subjectId) return;

        assessmentsCache = data.assessments || [];
        assessmentsForSubjectId = subjectId;
        select.innerHTML = '<option value="">Select an assessment</option>' +
            assessmentsCache.map((assessment) => `
                <option value="${assessment.assessment_id}">
                    ${escapeHtml(assessment.assessment_name)}
                    (Max ${formatNumber(assessment.max_marks)})
                </option>
            `).join("");

        updateMarksMaximum();
    } catch (error) {
        if (requestId !== assessmentsRequestId) return;
        select.innerHTML = '<option value="">Could not load assessments</option>';
        setMessage("marksMessage", error.message, "error");
    }
}

function updateMarksMaximum() {
    const assessmentId = Number($("marksAssessment").value);
    const assessment = assessmentsCache.find(
        (item) => Number(item.assessment_id) === assessmentId
    );

    if (!assessment) {
        $("marksMaximum").textContent = "Maximum: —";
        $("marksObtained").removeAttribute("max");
        return;
    }

    $("marksMaximum").textContent = `Maximum: ${assessment.max_marks}`;
    $("marksObtained").max = assessment.max_marks;
}

async function loadExistingMarks() {
    const requestId = ++marksRequestId;
    const studentId = Number($("marksStudent").value);
    const subjectId = Number($("subjectId").value);
    const body = $("existingMarksBody");

    if (!Number.isInteger(studentId) || studentId <= 0) {
        body.innerHTML =
            '<tr><td colspan="4" class="empty-cell">Select a student first.</td></tr>';
        return;
    }

    if (!Number.isInteger(subjectId) || subjectId <= 0) {
        body.innerHTML =
            '<tr><td colspan="4" class="empty-cell">Select a specific subject in the top bar to view marks.</td></tr>';
        return;
    }

    body.innerHTML =
        '<tr><td colspan="4" class="empty-cell">Loading marks...</td></tr>';

    try {
        const data = await apiRequest(`/api/marks?student_id=${studentId}`);
        if (requestId !== marksRequestId || Number($("marksStudent").value) !== studentId) return;
        existingMarksCache = data.marks || [];

        const relevantMarks = existingMarksCache.filter(
            (mark) => Number(mark.branch_subject_id) === subjectId
        );

        if (!relevantMarks.length) {
            body.innerHTML =
                '<tr><td colspan="4" class="empty-cell">No marks recorded for this subject.</td></tr>';
            return;
        }

        body.innerHTML = relevantMarks.map((mark) => `
            <tr>
                <td>${escapeHtml(mark.assessment_name)}</td>
                <td>${formatNumber(mark.marks_obtained)}</td>
                <td>${formatNumber(mark.max_marks)}</td>
                <td>
                    <button class="secondary-button edit-mark-button"
                        type="button" data-mark-id="${mark.mark_id}">
                        Edit
                    </button>
                </td>
            </tr>
        `).join("");

        body.querySelectorAll(".edit-mark-button").forEach((button) => {
            button.addEventListener("click", () => {
                const mark = relevantMarks.find(
                    (item) => Number(item.mark_id) === Number(button.dataset.markId)
                );

                if (!mark) return;

                $("marksAssessment").value = String(mark.assessment_id);
                $("marksObtained").value = mark.marks_obtained;
                $("marksMode").value = "update";
                $("marksForm").dataset.markId = mark.mark_id;
                updateMarksMaximum();
                setMessage("marksMessage", "Existing marks loaded for editing.");
            });
        });
    } catch (error) {
        if (requestId === marksRequestId) {
            body.innerHTML =
                `<tr><td colspan="4" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
        }
    }
}

async function saveMarks(event) {
    event.preventDefault();

    const studentId = Number($("marksStudent").value);
    const assessmentId = Number($("marksAssessment").value);
    const marksValue = $("marksObtained").value.trim();
    const assessment = assessmentsCache.find(
        (item) => Number(item.assessment_id) === assessmentId
    );

    if (!studentId || !assessment || marksValue === "") {
        setMessage("marksMessage", "Select a student, assessment, and enter marks.", "error");
        return;
    }

    const obtained = Number(marksValue);
    const maximum = Number(assessment.max_marks);

    if (!Number.isFinite(obtained) || obtained < 0 ||
        obtained > maximum || !/^\d+(\.\d{1,2})?$/.test(marksValue)) {
        setMessage(
            "marksMessage",
            `Enter a valid mark from 0 to ${maximum}, with at most two decimal places.`,
            "error"
        );
        return;
    }

    const mode = $("marksMode").value;
    const selectedMarkId = $("marksForm").dataset.markId;
    const existing = existingMarksCache.find(
        (mark) =>
            Number(mark.student_id) === studentId &&
            Number(mark.assessment_id) === assessmentId
    );

    let method = "POST";
    let path = "/api/marks";

    if (mode === "update") {
        if (!selectedMarkId || !existing ||
            Number(existing.mark_id) !== Number(selectedMarkId) ||
            Number(existing.assessment_id) !== assessmentId) {
            setMessage(
                "marksMessage",
                "Load the student's existing marks and click Edit before updating.",
                "error"
            );
            return;
        }

        method = "PUT";
        path = `/api/marks/${selectedMarkId}`;
    } else if (mode === "add") {
        if (existing) {
            setMessage(
                "marksMessage",
                "Marks already exist for this assessment. Choose Update and click Edit.",
                "error"
            );
            return;
        }
    } else if (existing) {
        method = "PUT";
        path = `/api/marks/${existing.mark_id}`;
    }

    $("saveMarksButton").disabled = true;
    setMessage("marksMessage", "Saving marks...");

    try {
        const result = await apiRequest(path, {
            method,
            body: JSON.stringify({
                student_id: studentId,
                assessment_id: assessmentId,
                marks_obtained: obtained
            })
        });

        delete $("marksForm").dataset.markId;
        $("marksMode").value = "auto";

        setMessage("marksMessage", result.message || "Marks saved successfully.", "success");

        await loadExistingMarks();
        await loadAnalytics();
    } catch (error) {
        setMessage("marksMessage", error.message, "error");
    } finally {
        $("saveMarksButton").disabled = false;
    }
}

$("marksAssessment").addEventListener("change", () => {
    updateMarksMaximum();
    if ($("marksMode").value === "update") {
        delete $("marksForm").dataset.markId;
        $("marksMode").value = "auto";
        setMessage("marksMessage", "Assessment changed. Select Edit again to update an existing record.");
    }
});
$("marksStudent").addEventListener("change", () => {
    delete $("marksForm").dataset.markId;
    $("marksMode").value = "auto";
    loadExistingMarks();
});
$("loadMarksButton").addEventListener("click", loadExistingMarks);
$("marksForm").addEventListener("submit", saveMarks);

$("overviewBranch")?.addEventListener("change", () => {
    populateOverviewSubjectDropdown();
    populateNewStudentBranchDropdown();
    populateAllSubjectsStudentDropdown();
    populateAttendanceStudentDropdown();
    populateDeleteStudentDropdown();
    delete $("marksForm").dataset.markId;
    $("marksMode").value = "auto";
    assessmentsForSubjectId = null;

    if (currentView === "marks") {
        ensureMarksSubjectSelected();
        populateMarksStudentDropdown();
        loadAssessments();
        loadExistingMarks();
    } else {
        populateMarksStudentDropdown();
    }

    if (currentView === "directory") {
        renderDirectory();
    }
    if (currentView === "attendance") {
        loadAttendance();
        loadDailyAttendanceRoster();
        loadExamAbsenceVerification();
        if (!$("examSchedulePanel")?.classList.contains("hidden")) loadBranchExamSchedule();
    }

    loadAnalytics();
});

$("subjectId").addEventListener("change", () => {
    delete $("marksForm").dataset.markId;
    $("marksMode").value = "auto";
    assessmentsForSubjectId = null;

    populateMarksStudentDropdown();
    populateAllSubjectsStudentDropdown();
    populateAttendanceStudentDropdown();
    populateDeleteStudentDropdown();

    if (currentView === "marks") {
        loadAssessments();
        loadExistingMarks();
    }
    if (currentView === "attendance") {
        loadAttendance();
        loadDailyAttendanceRoster();
        loadExamAbsenceVerification();
        if (!$("examSchedulePanel")?.classList.contains("hidden")) loadBranchExamSchedule();
    }

    loadAnalytics();
});

function populateSelect(id, values, labelPrefix = "") {
    const select = $(id); if (!select) return;
    const previous = select.value;
    const first = select.options[0]?.outerHTML || '<option value="">All</option>';
    select.innerHTML = first + values.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(labelPrefix + value)}</option>`).join("");
    if ([...select.options].some(o => o.value === previous)) select.value = previous;
}

async function loadFilterOptions() {
    if (!studentsLoaded) await loadStudents();
    const semesters = [...new Set(studentsCache.map(s => Number(s.semester)).filter(Number.isFinite))].sort((a,b)=>a-b);
    ["directorySemester", "attendanceSemester"].forEach(id => populateSelect(id, semesters, "Semester "));
}

function filteredStudents() {
    const selectedBranchId = $("overviewBranch")?.value ? Number($("overviewBranch").value) : null;
    const semester = $("directorySemester")?.value || "";
    const query = ($("directorySearch")?.value || "").trim().toLowerCase();

    return studentsCache.filter((s) =>
        (!selectedBranchId || Number(s.branch_id) === selectedBranchId) &&
        (!semester || String(s.semester) === semester) &&
        (!query ||
            String(s.student_id).includes(query) ||
            String(s.roll_no || "").toLowerCase().includes(query) ||
            String(s.name || "").toLowerCase().includes(query))
    );
}

async function deleteStudentById(studentId, studentLabel = "this student") {
    if (!confirm(`Are you sure you want to delete ${studentLabel} and all of their marks and attendance records?`)) {
        return;
    }

    setMessage("directoryMessage", "Deleting student...");
    try {
        const res = await apiRequest(`/api/students/${studentId}`, {
            method: "DELETE"
        });

        $("deleteStudentForm")?.classList.add("hidden");

        studentsLoaded = false;
        await loadStudents();
        await loadFilterOptions();
        renderDirectory();
        await loadAnalytics();

        setMessage("directoryMessage", res.message || "Student deleted successfully.", "success");
    } catch (error) {
        setMessage("directoryMessage", error.message, "error");
    }
}

function renderDirectory() {
    const body = $("directoryBody");
    if (!body) return;
    const allMatching = filteredStudents();
    const pageRows = paginateRows("directoryBody", allMatching, renderDirectory);

    body.innerHTML = pageRows.length
        ? pageRows.map((s) => `
            <tr>
                <td>${escapeHtml(s.student_id)}</td>
                <td>${escapeHtml(s.roll_no || "—")}</td>
                <td><strong>${escapeHtml(s.name)}</strong></td>
                <td>${escapeHtml(s.branch_code || "—")}</td>
                <td>Semester ${escapeHtml(s.semester ?? "—")}</td>
                <td>
                    <button type="button" class="secondary-button row-delete-btn"
                        data-student-id="${s.student_id}"
                        data-student-label="${escapeHtml(s.name)} (${escapeHtml(s.roll_no)})">
                        Delete
                    </button>
                </td>
            </tr>
        `).join("")
        : '<tr><td colspan="6" class="empty-cell">No students match these filters.</td></tr>';

    body.querySelectorAll(".row-delete-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            deleteStudentById(Number(btn.dataset.studentId), btn.dataset.studentLabel);
        });
    });

    setMessage("directoryMessage", `${allMatching.length} total matching student(s).`);
}

// Add Student Form handlers
$("toggleAddStudentBtn")?.addEventListener("click", async () => {
    $("deleteStudentForm")?.classList.add("hidden");
    const form = $("addStudentForm");
    if (!form) return;
    await loadPerformanceFilters();
    populateNewStudentBranchDropdown();
    form.classList.toggle("hidden");
    if (!form.classList.contains("hidden")) {
        $("newStudentName")?.focus();
    }
});

$("cancelAddStudentBtn")?.addEventListener("click", () => {
    $("addStudentForm")?.classList.add("hidden");
});

$("addStudentForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const branchId = Number($("newStudentBranch").value);
    const name = $("newStudentName").value.trim();
    const semester = Number($("newStudentSemester")?.value || 3);

    if (!branchId || !name) {
        setMessage("directoryMessage", "Please select a Branch and enter a Student Name.", "error");
        return;
    }

    $("saveNewStudentBtn").disabled = true;
    setMessage("directoryMessage", "Adding student...");

    try {
        const res = await apiRequest("/api/students", {
            method: "POST",
            body: JSON.stringify({
                branch_id: branchId,
                name: name,
                semester: semester
            })
        });

        $("newStudentName").value = "";
        $("addStudentForm").classList.add("hidden");

        studentsLoaded = false;
        await loadStudents();
        await loadFilterOptions();
        renderDirectory();
        await loadAnalytics();

        if ($("attStudentSelect") && res.student_id) {
            $("attStudentSelect").value = String(res.student_id);
        }
        if ($("allSubjectsStudentSelect") && res.student_id) {
            $("allSubjectsStudentSelect").value = String(res.student_id);
        }

        setMessage(
            "directoryMessage",
            `Student "${name}" (${res.roll_no}) added! Click "+ Add / Update attendance" in the Attendance tab to enter their subject periods.`,
            "success"
        );
    } catch (error) {
        setMessage("directoryMessage", error.message, "error");
    } finally {
        $("saveNewStudentBtn").disabled = false;
    }
});

// Delete Student Form handlers
$("toggleDeleteStudentBtn")?.addEventListener("click", () => {
    $("addStudentForm")?.classList.add("hidden");
    const form = $("deleteStudentForm");
    if (!form) return;
    populateDeleteStudentDropdown();
    form.classList.toggle("hidden");
});

$("cancelDeleteStudentBtn")?.addEventListener("click", () => {
    $("deleteStudentForm")?.classList.add("hidden");
});

$("deleteStudentForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const select = $("deleteStudentSelect");
    const studentId = Number(select?.value);

    if (!studentId) {
        setMessage("directoryMessage", "Please select a student to delete.", "error");
        return;
    }

    const label = select.options[select.selectedIndex]?.text || "this student";
    await deleteStudentById(studentId, label);
});

function attendanceStatus(percent) {
    if (percent > 75) return "Active";
    if (percent >= 65) return "Warning";
    return "Inactive";
}

function statusBadge(status) {
    const cls = status === "Active" ? "attendance-active" : status === "Warning" ? "attendance-warning" : "attendance-inactive";
    const symbol = status === "Active" ? "✓" : status === "Warning" ? "✓" : "!";
    const label = status === "Inactive" ? "Inactive · About to detain" : status;
    return `<span class="attendance-badge ${cls}"><span aria-hidden="true">${symbol}</span> ${label}</span>`;
}

function renderCumulativeAttendanceTable(records = null) {
    const body = $("attendanceBody");
    if (!body) return;
    const search = ($("attendanceSearch")?.value || "").trim().toLowerCase();

    const filtered = records || lastAttendanceRecords.filter((s) =>
        !search ||
        String(s.student_id).includes(search) ||
        String(s.roll_no || "").toLowerCase().includes(search) ||
        String(s.name || "").toLowerCase().includes(search)
    );

    const pageRows = paginateRows("attendanceBody", filtered, () => renderCumulativeAttendanceTable(null));

    body.innerHTML = pageRows.length
        ? pageRows.map((s) => `
            <tr>
                <td>${escapeHtml(s.student_id)}</td>
                <td>${escapeHtml(s.roll_no || "—")}</td>
                <td><strong>${escapeHtml(s.name)}</strong></td>
                <td>${escapeHtml(s.branch_code || "—")}</td>
                <td>Sem ${escapeHtml(s.semester ?? "—")}</td>
                <td>${escapeHtml(s.attended_periods ?? 0)} / ${escapeHtml(s.total_periods ?? 0)} periods</td>
                <td>${s.attendance_percentage == null ? "—" : `<strong>${Number(s.attendance_percentage).toFixed(1)}%</strong>`}</td>
                <td>${s.status ? statusBadge(s.status) : '<span class="muted">No attendance records</span>'}</td>
                <td>
                    <button type="button" class="secondary-button edit-att-btn"
                        data-student-id="${s.student_id}">
                        Edit
                    </button>
                </td>
            </tr>
        `).join("")
        : '<tr><td colspan="9" class="empty-cell">No students match these filters.</td></tr>';

    body.querySelectorAll(".edit-att-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            const sid = Number(btn.dataset.studentId);
            const panel = $("attendanceFormPanel");
            if (panel) panel.classList.remove("hidden");

            populateAttendanceStudentDropdown();
            if ($("attStudentSelect")) {
                if (![...$("attStudentSelect").options].some((o) => Number(o.value) === sid)) {
                    const st = studentsCache.find((item) => Number(item.student_id) === sid);
                    if (st) {
                        $("attStudentSelect").insertAdjacentHTML(
                            "beforeend",
                            `<option value="${st.student_id}">${escapeHtml(st.roll_no)} — ${escapeHtml(st.name)} (${escapeHtml(st.branch_code)})</option>`
                        );
                    }
                }
                $("attStudentSelect").value = String(sid);
            }
            loadStudentSubjectsForAttendanceForm(sid);
            panel?.scrollIntoView({ behavior: "smooth", block: "nearest" });
            setMessage("attRecordMessage", "Enter Periods Attended and Total Periods beside each subject — percentage calculates and saves automatically.");
        });
    });
}

async function loadAttendance() {
    if (!studentsLoaded) await loadStudents();
    const body = $("attendanceBody"); if (!body) return;

    const query = new URLSearchParams();
    const topBranchId = getSelectedBranchId();
    const topSubjectId = Number($("subjectId")?.value);
    if (topBranchId) query.set("branch_id", String(topBranchId));
    if (Number.isInteger(topSubjectId) && topSubjectId > 0) {
        query.set("branch_subject_id", String(topSubjectId));
    }
    if ($("attendanceSemester")?.value) query.set("semester", $("attendanceSemester").value);
    if ($("attendanceStatus")?.value) query.set("status", $("attendanceStatus").value);

    const search = ($("attendanceSearch")?.value || "").trim().toLowerCase();
    body.innerHTML = '<tr><td colspan="9" class="empty-cell">Loading attendance…</td></tr>';

    try {
        const data = await apiRequest(`/api/attendance?${query.toString()}`);
        lastAttendanceRecords = data.students || [];

        const records = lastAttendanceRecords.filter((s) =>
            !search ||
            String(s.student_id).includes(search) ||
            String(s.roll_no || "").toLowerCase().includes(search) ||
            String(s.name || "").toLowerCase().includes(search)
        );

        renderCumulativeAttendanceTable(records);

        $("attendanceTotal").textContent = records.length;
        $("attendanceActive").textContent = records.filter((s) => s.status === "Active").length;
        $("attendanceWarning").textContent = records.filter((s) => s.status === "Warning").length;
        $("attendanceInactive").textContent = records.filter((s) => s.status === "Inactive").length;

        renderAttendanceCharts(records);
        setMessage("attendanceMessage", data.message || "Attendance loaded.", "success");
    } catch (error) {
        body.innerHTML = `<tr><td colspan="9" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
        setMessage("attendanceMessage", error.message, "error");
    }
}

function renderAttendanceCharts(records) {
    const counts = ["Active", "Warning", "Inactive"].map((status) => ({
        status,
        count: records.filter((s) => s.status === status).length
    }));
    const total = counts.reduce((n, x) => n + x.count, 0);

    $("attendanceDistribution").innerHTML = total
        ? `<div class="distribution-list">${counts.map((x) => `
            <div class="distribution-row">
                <span>${x.status}</span>
                <div class="distribution-track">
                    <span class="distribution-fill ${x.status.toLowerCase()}" style="width:${(x.count / total) * 100}%"></span>
                </div>
                <strong>${x.count} (${((x.count / total) * 100).toFixed(0)}%)</strong>
            </div>
        `).join("")}</div>`
        : "No recorded attendance available for these students.";

    const data = records.filter((s) => s.attendance_percentage != null).slice(0, 12);
    $("attendanceBars").innerHTML = data.length
        ? `<div class="bar-list">${data.map((s) => `
            <div class="bar-row">
                <span title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</span>
                <div class="distribution-track">
                    <span class="distribution-fill ${attendanceStatus(Number(s.attendance_percentage)).toLowerCase()}" style="width:${Math.max(0, Math.min(100, Number(s.attendance_percentage)))}%"></span>
                </div>
                <strong>${Number(s.attendance_percentage).toFixed(1)}%</strong>
            </div>
        `).join("")}</div>`
        : "No recorded attendance percentages available.";
}

function renderAnalyticsCharts() {
    const passed = Number($("passedStudents").textContent) || 0;
    const failed = Number($("failedStudents").textContent) || 0;
    const incomplete = Number($("incompleteStudents").textContent) || 0;
    const total = passed + failed + incomplete;

    $("resultSummaryChart").innerHTML = total
        ? `<div class="distribution-list">${[
            { label: "Passed", n: passed, c: "active" },
            { label: "Failed", n: failed, c: "inactive" },
            { label: "Incomplete", n: incomplete, c: "warning" }
        ].map((x) => `
            <div class="distribution-row">
                <span>${x.label}</span>
                <div class="distribution-track">
                    <span class="distribution-fill ${x.c}" style="width:${(x.n / total) * 100}%"></span>
                </div>
                <strong>${x.n}</strong>
            </div>
        `).join("")}</div>`
        : "Load analytics to display the result summary.";

    const allStudents = lastAnalyticsData?.all_students || [];
    $("topPerformersChart").innerHTML = allStudents.length
        ? `<div class="bar-list">${allStudents.slice(0, 10).map((s) => {
            const val = Number(s.total_marks) || 0;
            const fillClass = s.result === "PASS" ? "active" : "inactive";
            return `
                <div class="bar-row">
                    <span title="${escapeHtml(s.student_name)}">${escapeHtml(s.student_name)}</span>
                    <div class="distribution-track">
                        <span class="distribution-fill ${fillClass}" style="width:${Math.min(100, Math.max(0, val))}%"></span>
                    </div>
                    <strong>${val.toFixed(1)}</strong>
                </div>
            `;
        }).join("")}</div>`
        : "No student scores available for this selection.";

    const diagBody = $("analyticsDiagnosticBody");
    if (diagBody) {
        const pageRows = paginateRows("analyticsDiagnosticBody", allStudents, renderAnalyticsCharts);
        diagBody.innerHTML = pageRows.length
            ? pageRows.map((s) => `
                <tr>
                    <td><strong>${escapeHtml(s.student_name)}</strong></td>
                    <td>${escapeHtml(s.roll_no)}</td>
                    <td>${formatNumber(s.total_marks)}</td>
                    <td>${pill(s.result)}</td>
                    <td class="reason-cell">${escapeHtml(s.failure_explanation || "—")}</td>
                </tr>
            `).join("")
            : '<tr><td colspan="5" class="empty-cell">No student records in this scope.</td></tr>';
    }
}

for (const id of ["directorySemester", "directorySearch"]) {
    $(id)?.addEventListener("input", renderDirectory);
    $(id)?.addEventListener("change", renderDirectory);
}
for (const id of ["attendanceRefresh", "attendanceSemester", "attendanceStatus"]) {
    $(id)?.addEventListener("click", loadAttendance);
    $(id)?.addEventListener("change", loadAttendance);
}
$("attendanceSearch")?.addEventListener("input", loadAttendance);
$("analyticsReload")?.addEventListener("click", loadAnalytics);

async function restoreSession() {
    try {
        const data = await apiRequest("/api/auth/me");
        showApp(data.user.username);
        await loadPerformanceFilters();
        showView(getSavedView());
        await loadAnalytics();
    } catch {
        // Show the AstraMindNexus Landing Page first when not signed in
        showLanding();
    }
}


let dailyRosterCache = [];
let dailyExamBannerCache = [];

function getActiveLeaveCategoryFilter() {
    return ($("dailyLeaveCategoryFilter")?.value || "").trim();
}

function getFilteredDailyRoster() {
    const search = ($("dailyRosterSearch")?.value || "").trim().toLowerCase();
    const leaveFilter = getActiveLeaveCategoryFilter();

    return dailyRosterCache.filter((st) => {
        const matchesSearch =
            !search ||
            String(st.roll_no || "").toLowerCase().includes(search) ||
            String(st.name || "").toLowerCase().includes(search) ||
            String(st.leave_category || "").toLowerCase().includes(search);

        if (!matchesSearch) return false;
        if (!leaveFilter) return true;

        if (leaveFilter === "ALL_LEAVES") {
            return Boolean(st.leave_category);
        }
        if (leaveFilter === "PENDING_LEAVES") {
            return Boolean(st.leave_category) && st.leave_status === "PENDING";
        }
        if (leaveFilter === "APPROVED_LEAVES") {
            return st.leave_status === "APPROVED";
        }
        return st.leave_category === leaveFilter;
    });
}

function updateDailyRosterSummaryCounts() {
    let fullPresent = 0;
    let approvedLeave = 0;
    let absent = 0;
    let pendingLeave = 0;
    let medCount = 0;
    let nssCount = 0;
    let sportsCount = 0;

    dailyRosterCache.forEach((st) => {
        if (st.leave_category === "Medical Leave") medCount++;
        if (st.leave_category === "NSS") nssCount++;
        if (st.leave_category === "Sports") sportsCount++;

        if (st.leave_status === "APPROVED") {
            approvedLeave++;
        } else if (st.attendance_status === "PRESENT") {
            fullPresent++;
        } else if (st.attendance_status === "ABSENT") {
            absent++;
        }

        if (st.leave_category && st.leave_status === "PENDING") {
            pendingLeave++;
        }
    });

    const filtered = getFilteredDailyRoster();
    if ($("dailyTotalCount")) $("dailyTotalCount").textContent = filtered.length;
    if ($("dailyPresentCount")) $("dailyPresentCount").textContent = fullPresent;
    if ($("dailyApprovedLeaveCount")) $("dailyApprovedLeaveCount").textContent = approvedLeave;
    if ($("dailyAbsentCount")) $("dailyAbsentCount").textContent = absent;
    if ($("dailyPendingLeaveCount")) $("dailyPendingLeaveCount").textContent = pendingLeave;

    if ($("countMedicalLeave")) $("countMedicalLeave").textContent = medCount;
    if ($("countNSS")) $("countNSS").textContent = nssCount;
    if ($("countSports")) $("countSports").textContent = sportsCount;
    if ($("countPendingLeaves")) $("countPendingLeaves").textContent = pendingLeave;
    if ($("countApprovedLeaves")) $("countApprovedLeaves").textContent = approvedLeave;
}

function formatLeaveCategoryBadge(st) {
    const cat = st.leave_category;
    const status = st.leave_status || "NONE";
    if (!cat) {
        return '<span class="leave-badge none">Regular (No Leave Request)</span>';
    }
    const cls = cat === "Medical Leave" ? "medical" : cat === "NSS" ? "nss" : "sports";
    const icon = cat === "Medical Leave" ? "🏥" : cat === "NSS" ? "🎖" : "🏅";
    const statusText = status === "APPROVED"
        ? "✓ Approved (0.6/1)"
        : status === "REJECTED"
        ? "✕ Rejected"
        : "⏳ Pending Request";
    return `
        <span class="leave-badge ${cls}" title="${escapeHtml(st.leave_reason || cat)}">
            ${icon} ${escapeHtml(cat)} · <strong>${statusText}</strong>
        </span>
    `;
}

function formatCreditBadge(st) {
    const status = st.leave_status || "NONE";
    if (status === "APPROVED") {
        return '<span class="credit-badge partial">0.6 / 1 (60%)</span>';
    }
    if (st.attendance_status === "PRESENT") {
        return '<span class="credit-badge full">1.0 / 1 (100%)</span>';
    }
    return '<span class="credit-badge zero">0.0 / 1 (0%)</span>';
}

function renderDailyRosterTable() {
    const body = $("dailyRosterBody");
    if (!body) return;

    const filtered = getFilteredDailyRoster();
    if ($("dailyTotalCount")) $("dailyTotalCount").textContent = filtered.length;

    const pageRows = paginateRows("dailyRosterBody", filtered, renderDailyRosterTable);

    if (!filtered.length) {
        body.innerHTML = '<tr><td colspan="7" class="empty-cell">No students match this filter. Try switching the Medical Leave / NSS / Sports filter above.</td></tr>';
        return;
    }

    body.innerHTML = pageRows.map((st) => {
        const isApprovedLeave = st.leave_status === "APPROVED";
        const isP = st.attendance_status === "PRESENT" && !isApprovedLeave;
        const isA = st.attendance_status === "ABSENT";
        const isPendingLeave = Boolean(st.leave_category) && st.leave_status === "PENDING";

        const examLabel = st.scheduled_exam
            ? `<strong>${escapeHtml(st.scheduled_exam)}</strong>`
            : '<span class="muted">Regular Class</span>';

        const popupBtnLabel = isPendingLeave
            ? `★ Review ${escapeHtml(st.leave_category)} (0.6/1)`
            : isApprovedLeave
            ? `✓ ${escapeHtml(st.leave_category)} (0.6/1)`
            : `⚡ NSS / Sports / Medical`;

        return `
            <tr data-student-id="${st.student_id}">
                <td><strong>${escapeHtml(st.roll_no)}</strong></td>
                <td><strong>${escapeHtml(st.name)}</strong></td>
                <td>${escapeHtml(st.branch_code)} · Sem ${escapeHtml(st.semester)}</td>
                <td>${examLabel}</td>
                <td>${formatLeaveCategoryBadge(st)}</td>
                <td>${formatCreditBadge(st)}</td>
                <td>
                    <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                        <div class="status-toggle-group">
                            <button type="button" class="status-toggle-btn present ${isP ? "selected" : ""}"
                                data-student-id="${st.student_id}" data-status="PRESENT" title="Regular Present (1.0/1 · 100%)">
                                ✓ Present
                            </button>
                            <button type="button" class="status-toggle-btn absent ${isA ? "selected" : ""}"
                                data-student-id="${st.student_id}" data-status="ABSENT" title="Absent (0/1 · 0%)">
                                ✕ Absent
                            </button>
                        </div>
                        <button type="button"
                            class="popup-trigger-btn ${isPendingLeave ? "pending-highlight" : ""}"
                            data-open-leave-modal="${st.student_id}">
                            ${popupBtnLabel}
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join("");

    // Standard Present (1.0) / Absent (0.0) toggle buttons
    body.querySelectorAll(".status-toggle-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const sid = Number(btn.dataset.studentId);
            const newStatus = btn.dataset.status;
            const dateStr = $("dailyAttendanceDate")?.value || "2026-08-10";

            const stObj = dailyRosterCache.find((item) => Number(item.student_id) === sid);
            if (stObj) {
                stObj.attendance_status = newStatus;
                if (stObj.leave_status === "APPROVED") {
                    stObj.leave_status = stObj.leave_category ? "PENDING" : "NONE";
                }
                stObj.attendance_credit = newStatus === "PRESENT" ? 1.0 : 0.0;
            }

            updateDailyRosterSummaryCounts();
            renderDailyRosterTable();

            try {
                await apiRequest("/api/attendance/daily", {
                    method: "POST",
                    body: JSON.stringify({
                        attendance_date: dateStr,
                        records: [{
                            student_id: sid,
                            attendance_status: newStatus,
                            leave_status: stObj?.leave_status || "NONE",
                            attendance_credit: newStatus === "PRESENT" ? 1.0 : 0.0
                        }]
                    })
                });
                setMessage("dailyRosterMessage", `Saved ${stObj?.roll_no || "student"} as ${newStatus} (${newStatus === "PRESENT" ? "1.0/1" : "0/1"}) on ${dateStr}.`, "success");
                loadExamAbsenceVerification();
            } catch (error) {
                setMessage("dailyRosterMessage", error.message, "error");
            }
        });
    });

    // Pop-up modal trigger button on every student row
    body.querySelectorAll("[data-open-leave-modal]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const sid = Number(btn.dataset.openLeaveModal);
            openLeaveApprovalModal(sid);
        });
    });
}

function openLeaveApprovalModal(studentId) {
    const st = dailyRosterCache.find((item) => Number(item.student_id) === Number(studentId));
    const modal = $("leaveApprovalModal");
    if (!st || !modal) return;

    const dateStr = $("dailyAttendanceDate")?.value || "2026-08-10";
    $("modalStudentId").value = String(st.student_id);
    $("modalStudentName").textContent = st.name;
    $("modalStudentMeta").textContent = `${st.roll_no} · ${st.branch_code} (Sem ${st.semester})`;
    $("modalAttendanceDateLabel").textContent = dateStr;

    const catSelect = $("modalLeaveCategory");
    if (catSelect) {
        catSelect.value = st.leave_category || "Medical Leave";
    }

    const defaultReasons = {
        "Medical Leave": "Medical certificate verified for fever / hospital visit",
        "NSS": "NSS Community Service & Blood Donation Camp representation",
        "Sports": "Inter-University Zonal Sports Club tournament representation"
    };
    const reasonInput = $("modalLeaveReason");
    if (reasonInput) {
        reasonInput.value = st.leave_reason || defaultReasons[catSelect?.value || "Medical Leave"];
    }

    modal.classList.remove("hidden");
}

function closeLeaveApprovalModal() {
    $("leaveApprovalModal")?.classList.add("hidden");
}

$("modalLeaveCategory")?.addEventListener("change", () => {
    const cat = $("modalLeaveCategory").value;
    const defaultReasons = {
        "Medical Leave": "Medical certificate verified for fever / hospital visit",
        "NSS": "NSS Community Service & Blood Donation Camp representation",
        "Sports": "Inter-University Zonal Sports Club tournament representation"
    };
    const reasonInput = $("modalLeaveReason");
    if (reasonInput) {
        reasonInput.value = defaultReasons[cat] || "";
    }
});

$("closeLeaveModalBtn")?.addEventListener("click", closeLeaveApprovalModal);
$("modalCancelBtn")?.addEventListener("click", closeLeaveApprovalModal);
$("leaveApprovalModal")?.addEventListener("click", (e) => {
    if (e.target === $("leaveApprovalModal")) closeLeaveApprovalModal();
});

// Submit Pop-up Modal: Approve as Present (0.6/1 · 60% credit)
$("leaveApprovalForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const sid = Number($("modalStudentId")?.value);
    const cat = $("modalLeaveCategory")?.value || "Medical Leave";
    const reason = ($("modalLeaveReason")?.value || "").trim();
    const dateStr = $("dailyAttendanceDate")?.value || "2026-08-10";

    if (!sid) return;

    $("modalApproveBtn").disabled = true;
    try {
        const res = await apiRequest("/api/attendance/leave-approve", {
            method: "POST",
            body: JSON.stringify({
                attendance_date: dateStr,
                action: "APPROVE",
                student_id: sid,
                leave_category: cat,
                leave_reason: reason
            })
        });

        const stObj = dailyRosterCache.find((item) => Number(item.student_id) === sid);
        if (stObj) {
            stObj.attendance_status = "PRESENT";
            stObj.leave_category = cat;
            stObj.leave_status = "APPROVED";
            stObj.leave_reason = reason;
            stObj.attendance_credit = 0.60;
        }

        closeLeaveApprovalModal();
        updateDailyRosterSummaryCounts();
        renderDailyRosterTable();
        setMessage("dailyRosterMessage", res.message || `Approved ${cat} (0.6/1 · 60% credit).`, "success");
        await loadAttendance();
        await loadExamAbsenceVerification();
    } catch (error) {
        setMessage("dailyRosterMessage", error.message, "error");
    } finally {
        $("modalApproveBtn").disabled = false;
    }
});

// Reject button inside Pop-up Modal
$("modalRejectBtn")?.addEventListener("click", async () => {
    const sid = Number($("modalStudentId")?.value);
    const cat = $("modalLeaveCategory")?.value || "Medical Leave";
    const reason = ($("modalLeaveReason")?.value || "").trim();
    const dateStr = $("dailyAttendanceDate")?.value || "2026-08-10";

    if (!sid) return;

    $("modalRejectBtn").disabled = true;
    try {
        const res = await apiRequest("/api/attendance/leave-approve", {
            method: "POST",
            body: JSON.stringify({
                attendance_date: dateStr,
                action: "REJECT",
                student_id: sid,
                leave_category: cat,
                leave_reason: reason
            })
        });

        const stObj = dailyRosterCache.find((item) => Number(item.student_id) === sid);
        if (stObj) {
            stObj.attendance_status = "ABSENT";
            stObj.leave_category = cat;
            stObj.leave_status = "REJECTED";
            stObj.leave_reason = reason;
            stObj.attendance_credit = 0.00;
        }

        closeLeaveApprovalModal();
        updateDailyRosterSummaryCounts();
        renderDailyRosterTable();
        setMessage("dailyRosterMessage", res.message || `Rejected ${cat} request (0/1 credit).`, "success");
        await loadAttendance();
        await loadExamAbsenceVerification();
    } catch (error) {
        setMessage("dailyRosterMessage", error.message, "error");
    } finally {
        $("modalRejectBtn").disabled = false;
    }
});

// Exclusive Bulk Approval for Filtered Medical Leave / NSS / Sports Club
$("approveFilteredLeavesBtn")?.addEventListener("click", async () => {
    const dateStr = $("dailyAttendanceDate")?.value || "2026-08-10";
    const activeFilter = getActiveLeaveCategoryFilter();
    const visibleRows = getFilteredDailyRoster();

    // Target students who have a leave category in the current filter
    const targets = visibleRows.filter((st) => Boolean(st.leave_category));
    if (!targets.length) {
        setMessage(
            "dailyRosterMessage",
            "No Medical Leave, NSS, or Sports requests in the current filter. Select a category filter first or click a student's pop-up button.",
            "error"
        );
        return;
    }

    const filterLabel = activeFilter && !activeFilter.includes("_")
        ? activeFilter
        : "Medical Leave / NSS / Sports";

    $("approveFilteredLeavesBtn").disabled = true;
    setMessage("dailyRosterMessage", `Approving ${targets.length} ${filterLabel} request(s) with 0.6/1 (60%) credit…`);

    try {
        const payloadStudents = targets.map((st) => ({
            student_id: st.student_id,
            leave_category: st.leave_category,
            leave_reason: st.leave_reason || `Approved ${st.leave_category} (0.6/1 credit)`
        }));

        const res = await apiRequest("/api/attendance/leave-approve", {
            method: "POST",
            body: JSON.stringify({
                attendance_date: dateStr,
                action: "APPROVE",
                students: payloadStudents
            })
        });

        targets.forEach((st) => {
            st.attendance_status = "PRESENT";
            st.leave_status = "APPROVED";
            st.attendance_credit = 0.60;
        });

        updateDailyRosterSummaryCounts();
        renderDailyRosterTable();
        setMessage("dailyRosterMessage", res.message || `Approved ${targets.length} student(s) with 0.6/1 (60%) credit!`, "success");
        await loadAttendance();
        await loadExamAbsenceVerification();
    } catch (error) {
        setMessage("dailyRosterMessage", error.message, "error");
    } finally {
        $("approveFilteredLeavesBtn").disabled = false;
    }
});

async function loadDailyAttendanceRoster() {
    const dateInput = $("dailyAttendanceDate");
    const dateStr = (dateInput?.value || "2026-08-10").trim();
    const branchId = getSelectedBranchId();
    const body = $("dailyRosterBody");
    if (!body) return;

    document.querySelectorAll(".exam-chip").forEach((chip) => {
        chip.classList.toggle("active", chip.dataset.date === dateStr);
    });

    body.innerHTML = '<tr><td colspan="7" class="empty-cell">Loading daily roll-call roster &amp; special leave requests…</td></tr>';

    try {
        const query = new URLSearchParams({ date: dateStr });
        if (branchId) query.set("branch_id", String(branchId));

        const data = await apiRequest(`/api/attendance/daily?${query.toString()}`);
        dailyRosterCache = data.students || [];
        dailyExamBannerCache = data.exams_on_date || [];

        const banner = $("dailyExamBanner");
        if (banner) {
            if (dailyExamBannerCache.length) {
                const labels = dailyExamBannerCache.map(
                    (e) => `<strong>${escapeHtml(e.branch_code)}</strong>: ${escapeHtml(e.subject_code)} (${escapeHtml(e.assessment_name)})`
                ).join(" &nbsp;|&nbsp; ");
                banner.innerHTML = `📅 <strong>Scheduled Examination Date (${escapeHtml(dateStr)}):</strong> ${labels}`;
                banner.classList.remove("hidden");
            } else {
                banner.classList.add("hidden");
            }
        }

        updateDailyRosterSummaryCounts();
        renderDailyRosterTable();
        setMessage("dailyRosterMessage", `Loaded ${dailyRosterCache.length} student(s) for ${dateStr}.`, "success");
    } catch (error) {
        body.innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
}

async function bulkMarkDailyRoster(targetStatus) {
    const affected = getFilteredDailyRoster();
    if (!affected.length) return;

    affected.forEach((st) => {
        st.attendance_status = targetStatus;
        if (targetStatus === "PRESENT" && st.leave_status !== "APPROVED") {
            st.attendance_credit = 1.00;
        } else if (targetStatus === "ABSENT") {
            if (st.leave_status === "APPROVED") st.leave_status = "PENDING";
            st.attendance_credit = 0.00;
        }
    });

    updateDailyRosterSummaryCounts();
    renderDailyRosterTable();
    await saveEntireDailyRoster(affected);
}

async function saveEntireDailyRoster(subset = null) {
    const dateStr = ($("dailyAttendanceDate")?.value || "2026-08-10").trim();
    const listToSave = subset || dailyRosterCache;
    const records = listToSave
        .filter((st) => st.attendance_status === "PRESENT" || st.attendance_status === "ABSENT")
        .map((st) => ({
            student_id: st.student_id,
            attendance_status: st.attendance_status,
            leave_category: st.leave_category || null,
            leave_status: st.leave_status || "NONE",
            leave_reason: st.leave_reason || null,
            attendance_credit: st.leave_status === "APPROVED" ? 0.60 : (st.attendance_status === "PRESENT" ? 1.00 : 0.00)
        }));

    if (!records.length) {
        setMessage("dailyRosterMessage", "Mark at least one student Present or Absent first.", "error");
        return;
    }

    $("saveDailyRosterBtn").disabled = true;
    setMessage("dailyRosterMessage", `Saving ${records.length} attendance records for ${dateStr}…`);

    try {
        const res = await apiRequest("/api/attendance/daily", {
            method: "POST",
            body: JSON.stringify({
                attendance_date: dateStr,
                records
            })
        });
        setMessage("dailyRosterMessage", res.message || "Daily attendance saved!", "success");
        await loadExamAbsenceVerification();
    } catch (error) {
        setMessage("dailyRosterMessage", error.message, "error");
    } finally {
        $("saveDailyRosterBtn").disabled = false;
    }
}

async function loadBranchExamSchedule() {
    const body = $("examScheduleBody");
    if (!body) return;
    const branchId = getSelectedBranchId();
    const assessType = $("scheduleAssessmentFilter")?.value || "";

    const query = new URLSearchParams();
    if (branchId) query.set("branch_id", String(branchId));
    if (assessType) query.set("assessment_name", assessType);

    body.innerHTML = '<tr><td colspan="8" class="empty-cell">Loading branch exam dates…</td></tr>';

    try {
        const data = await apiRequest(`/api/attendance/exam-schedule?${query.toString()}`);
        const rows = data.schedule || [];

        if (!rows.length) {
            body.innerHTML = '<tr><td colspan="8" class="empty-cell">No exam schedule entries found.</td></tr>';
            return;
        }

        body.innerHTML = rows.map((r) => `
            <tr>
                <td><strong>${escapeHtml(r.branch_code)}</strong></td>
                <td>Sem ${escapeHtml(r.semester)}</td>
                <td><strong>${escapeHtml(r.subject_code)}</strong></td>
                <td>${escapeHtml(r.subject_name)}</td>
                <td>${escapeHtml(r.assessment_name)}</td>
                <td>${formatNumber(r.max_marks)}</td>
                <td>
                    <input type="date" class="schedule-date-input"
                        data-assessment-id="${r.assessment_id}"
                        value="${escapeHtml(r.exam_date || "2026-08-10")}">
                </td>
                <td>
                    <button type="button" class="secondary-button jump-exam-date-btn"
                        data-date="${escapeHtml(r.exam_date || "2026-08-10")}">
                        Open Roll Call →
                    </button>
                </td>
            </tr>
        `).join("");

        body.querySelectorAll(".schedule-date-input").forEach((inp) => {
            inp.addEventListener("change", async () => {
                const aid = Number(inp.dataset.assessmentId);
                const newDt = inp.value;
                if (!aid || !newDt) return;
                try {
                    const res = await apiRequest("/api/attendance/exam-schedule", {
                        method: "POST",
                        body: JSON.stringify({ assessment_id: aid, exam_date: newDt })
                    });
                    setMessage("examScheduleMessage", res.message || "Exam date updated.", "success");
                    loadDailyAttendanceRoster();
                    loadExamAbsenceVerification();
                } catch (err) {
                    setMessage("examScheduleMessage", err.message, "error");
                }
            });
        });

        body.querySelectorAll(".jump-exam-date-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                const dt = btn.dataset.date;
                if ($("dailyAttendanceDate")) {
                    $("dailyAttendanceDate").value = dt;
                    loadDailyAttendanceRoster();
                    $("dailyAttendanceDate").scrollIntoView({ behavior: "smooth", block: "center" });
                }
            });
        });
    } catch (error) {
        body.innerHTML = `<tr><td colspan="8" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
}

let examVerificationRecordsCache = [];

function renderExamVerificationTable() {
    const body = $("examVerificationBody");
    if (!body) return;

    const pageRows = paginateRows("examVerificationBody", examVerificationRecordsCache, renderExamVerificationTable);

    if (!examVerificationRecordsCache.length) {
        body.innerHTML = '<tr><td colspan="9" class="empty-cell">All students attempted their exams in this scope!</td></tr>';
        return;
    }

    body.innerHTML = pageRows.map((r) => {
        const attDay = r.attendance_on_exam_day;
        const lvStatus = r.leave_status || "NONE";
        const lvCat = r.leave_category;

        const attBadge = lvStatus === "APPROVED"
            ? `<span class="credit-badge partial">PRESENT (${escapeHtml(lvCat)} · 0.6/1)</span>`
            : attDay === "ABSENT"
            ? '<span class="result-pill fail">ABSENT (0/1)</span>'
            : attDay === "PRESENT"
            ? '<span class="result-pill incomplete">PRESENT (1/1)</span>'
            : '<span class="muted">UNMARKED</span>';

        const verdictBadge = lvStatus === "APPROVED"
            ? `<span class="attendance-badge attendance-active">✓ Approved ${escapeHtml(lvCat)} (60% Credit)</span>`
            : attDay === "ABSENT"
            ? '<span class="attendance-badge attendance-active">✓ Verified Absent on Exam Day</span>'
            : attDay === "PRESENT"
            ? '<span class="attendance-badge attendance-inactive">! Present on Campus · Skipped Exam</span>'
            : '<span class="attendance-badge attendance-warning">Pending Daily Roll Call</span>';

        return `
            <tr>
                <td><strong>${escapeHtml(r.roll_no)}</strong></td>
                <td>${escapeHtml(r.student_name)}</td>
                <td>${escapeHtml(r.branch_code)}</td>
                <td>${escapeHtml(r.subject_code)}</td>
                <td><strong>${escapeHtml(r.assessment_name)}</strong></td>
                <td>${escapeHtml(r.exam_date || "—")}</td>
                <td>${attBadge}</td>
                <td>${verdictBadge}</td>
                <td>
                    <button type="button" class="secondary-button verify-mark-absent-btn"
                        data-student-id="${r.student_id}"
                        data-exam-date="${escapeHtml(r.exam_date || "2026-08-10")}">
                        Mark Absent on ${escapeHtml(r.exam_date || "Exam Day")}
                    </button>
                </td>
            </tr>
        `;
    }).join("");

    body.querySelectorAll(".verify-mark-absent-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const sid = Number(btn.dataset.studentId);
            const dt = btn.dataset.examDate;
            try {
                await apiRequest("/api/attendance/daily", {
                    method: "POST",
                    body: JSON.stringify({
                        attendance_date: dt,
                        records: [{ student_id: sid, attendance_status: "ABSENT", attendance_credit: 0.0 }]
                    })
                });
                await loadExamAbsenceVerification();
                if ($("dailyAttendanceDate")?.value === dt) {
                    loadDailyAttendanceRoster();
                }
            } catch (err) {
                setMessage("attendanceMessage", err.message, "error");
            }
        });
    });
}

async function loadExamAbsenceVerification() {
    const body = $("examVerificationBody");
    if (!body) return;
    const branchId = getSelectedBranchId();
    const assessName = $("verifyAssessmentFilter")?.value || "";

    const query = new URLSearchParams();
    if (branchId) query.set("branch_id", String(branchId));
    if (assessName) query.set("assessment_name", assessName);

    try {
        const data = await apiRequest(`/api/attendance/exam-verification?${query.toString()}`);
        const summary = data.summary || {};
        examVerificationRecordsCache = data.records || [];

        if ($("verifyTotalCount")) $("verifyTotalCount").textContent = summary.total_unattempted ?? 0;
        if ($("verifyAbsentCount")) $("verifyAbsentCount").textContent = summary.verified_absent ?? 0;
        if ($("verifyAnomalyCount")) $("verifyAnomalyCount").textContent = summary.present_skipped_anomaly ?? 0;

        resetTablePage("examVerificationBody");
        renderExamVerificationTable();
    } catch (error) {
        body.innerHTML = `<tr><td colspan="9" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
}

$("loadDailyRosterBtn")?.addEventListener("click", loadDailyAttendanceRoster);
$("dailyAttendanceDate")?.addEventListener("change", loadDailyAttendanceRoster);
$("dailyRosterSearch")?.addEventListener("input", renderDailyRosterTable);

// Sync the Exclusive Category Filter Dropdown and Filter Pills
$("dailyLeaveCategoryFilter")?.addEventListener("change", () => {
    const val = $("dailyLeaveCategoryFilter").value;
    document.querySelectorAll(".leave-filter-pill").forEach((pill) => {
        pill.classList.toggle("active", pill.dataset.leaveFilter === val);
    });
    renderDailyRosterTable();
});

document.querySelectorAll(".leave-filter-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
        const val = pill.dataset.leaveFilter || "";
        if ($("dailyLeaveCategoryFilter")) {
            $("dailyLeaveCategoryFilter").value = val;
        }
        document.querySelectorAll(".leave-filter-pill").forEach((p) => {
            p.classList.toggle("active", p.dataset.leaveFilter === val);
        });
        renderDailyRosterTable();
    });
});

$("markAllPresentBtn")?.addEventListener("click", () => bulkMarkDailyRoster("PRESENT"));
$("markAllAbsentBtn")?.addEventListener("click", () => bulkMarkDailyRoster("ABSENT"));
$("saveDailyRosterBtn")?.addEventListener("click", () => saveEntireDailyRoster());

document.querySelectorAll(".exam-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
        if ($("dailyAttendanceDate")) {
            $("dailyAttendanceDate").value = chip.dataset.date;
            loadDailyAttendanceRoster();
        }
    });
});

$("toggleExamScheduleBtn")?.addEventListener("click", () => {
    const panel = $("examSchedulePanel");
    if (!panel) return;
    panel.classList.toggle("hidden");
    if (!panel.classList.contains("hidden")) {
        loadBranchExamSchedule();
    }
});
$("closeExamScheduleBtn")?.addEventListener("click", () => {
    $("examSchedulePanel")?.classList.add("hidden");
});
$("scheduleAssessmentFilter")?.addEventListener("change", loadBranchExamSchedule);
$("verifyAssessmentFilter")?.addEventListener("change", loadExamAbsenceVerification);

$("exportExcelReportBtn")?.addEventListener("click", async () => {
    await resolveApiBaseUrl();
    const branchId = getSelectedBranchId();
    const dateStr = $("dailyAttendanceDate")?.value || "2026-08-10";
    const query = new URLSearchParams({ date: dateStr });
    if (branchId) query.set("branch_id", String(branchId));
    window.open(`${API_BASE_URL}/api/attendance/export-excel?${query.toString()}`, "_blank");
});

restoreSession();