create database if not exists student_performance_db;
use student_performance_db;

create table if not exists branches(
	branch_id int auto_increment primary key,
    branch_code varchar(20) not null unique,
    branch_name varchar(120) not null unique
);

INSERT INTO branches (branch_code, branch_name)
VALUES
('CSE', 'Computer Science Engineering'),
('CSE-AIML', 'CSE - Artificial Intelligence and Machine Learning'),
('CSE-DS', 'CSE - Data Science'),
('CSE-AIDS', 'CSE - Artificial Intelligence and Data Science'),
('CSE-CS', 'CSE-CS'),
('ECE', 'Electronics and Communication Engineering'),
('EEE', 'Electrical and Electronics Engineering'),
('CIVIL', 'Civil Engineering'),
('MECH', 'Mechanical Engineering')
ON DUPLICATE KEY UPDATE
    branch_code = branch_code;

create table if not exists student(
	student_id int auto_increment primary key,
    roll_no varchar(30) not null unique,
    name varchar(100) not null,
    branch_id int not null,
    semester tinyint unsigned not null,
    section varchar(20) not null,
    
    constraint fk_students_branch
		foreign key (branch_id)
        references branches(branch_id),
        
	constraint chk_student_semester
		check (semester between 1 and 12)
);

create table if not exists subjects(
	subject_id int auto_increment primary key,
    subject_code varchar(30) not null unique,
    subject_name varchar(120) not null
);

create table if not exists branch_subjects(
	branch_subject_id int auto_increment primary key,
    branch_id int not null,
    semester tinyint unsigned not null,
    subject_id int not null,
    max_marks decimal(6,2) not null,
    pass_marks decimal(6,2) not null,
    
    constraint fk_bs_branch
		foreign key (branch_id)
		references branches(branch_id),
        
	constraint fk_bs_subject
		foreign key (subject_id)
        references subjects(subject_id),
        
	constraint uq_branch_sem_subject
		unique(branch_id, semester, subject_id),
        
	constraint chk_bs_semester
		check (semester between 1 and 12),
        
	constraint chk_bs_marks
		check(max_marks > 0 and
				pass_marks >=0 and
                pass_marks <= max_marks)    
);

CREATE TABLE IF NOT EXISTS assessments (
    assessment_id INT AUTO_INCREMENT PRIMARY KEY,
    branch_subject_id INT NOT NULL,
    assessment_name VARCHAR(50) NOT NULL,
    max_marks DECIMAL(6,2) NOT NULL,

    CONSTRAINT fk_assessments_branch_subject
        FOREIGN KEY (branch_subject_id)
        REFERENCES branch_subjects(branch_subject_id),

    CONSTRAINT uq_assessment_name
        UNIQUE (branch_subject_id, assessment_name),

    CONSTRAINT chk_assessment_max_marks
        CHECK (max_marks > 0)
);

CREATE TABLE if not exists marks (
    mark_id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    assessment_id INT NOT NULL,
    marks_obtained DECIMAL(6,2) NOT NULL,

    CONSTRAINT fk_marks_student
        FOREIGN KEY (student_id)
        REFERENCES student(student_id),

    CONSTRAINT fk_marks_assessment
        FOREIGN KEY (assessment_id)
        REFERENCES assessments(assessment_id),

    CONSTRAINT uq_student_assessment
        UNIQUE (student_id, assessment_id),

    CONSTRAINT chk_marks_nonnegative
        CHECK (marks_obtained >= 0)
);

CREATE TABLE IF NOT EXISTS users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_user_role
        CHECK (role IN ('admin', 'user'))
);
      
INSERT INTO subjects (subject_code, subject_name)
VALUES
('CS301', 'Database Management Systems'),
('CS302', 'Operating Systems'),
('CS303', 'Computer Networks'),
('CS304', 'Design and Analysis of Algorithms')
ON DUPLICATE KEY UPDATE
    subject_code = subject_code;

INSERT INTO student
    (roll_no, name, branch_id, semester, section)
VALUES
    ('CSE001', 'Manish', 1, 3, 'A'),
    ('CSE002', 'Bipin Yadav', 1, 3, 'A'),
    ('CSE003', 'Arman', 1, 3, 'A'),
    ('CSE004', 'Tharun', 1, 3, 'A')
ON DUPLICATE KEY UPDATE
    roll_no = roll_no;

SELECT
    s.roll_no,
    s.name,
    sub.subject_code,
    a.assessment_name,
    m.marks_obtained,
    a.max_marks
FROM marks m
JOIN student s
    ON s.student_id = m.student_id
JOIN assessments a
    ON a.assessment_id = m.assessment_id
JOIN branch_subjects bs
    ON bs.branch_subject_id = a.branch_subject_id
JOIN subjects sub
    ON sub.subject_id = bs.subject_id
WHERE s.roll_no = 'CSE001'
ORDER BY a.assessment_id;

USE student_performance_db;

START TRANSACTION;

-- =========================================================================
-- STEP 1: Clear the 80 auto-generated attendance rows using the Primary Key
--         (Satisfies MySQL Workbench Safe Update Mode - Error 1175)
-- =========================================================================
DELETE FROM attendance_records WHERE attendance_id > 0;

-- =========================================================================
-- STEP 2: Insert Students (300 in each of the 5 CSE branches, 100 in ECE, EEE, MECH)
--         Uses INSERT IGNORE on unique roll_no so CSE001..CSE004 are preserved untouched.
-- =========================================================================
INSERT IGNORE INTO student (roll_no, name, branch_id, semester, section)
SELECT
    CONCAT(b.prefix, LPAD(seq.n, 3, '0')) AS roll_no,
    CONCAT(
        ELT(1 + ((seq.n * 3 + b.branch_id) % 24),
            'Aarav', 'Aditi', 'Akhil', 'Ananya', 'Arjun', 'Bhavya',
            'Charan', 'Deepak', 'Divya', 'Gautam', 'Harini', 'Ishaan',
            'Kalyan', 'Kavya', 'Keerthi', 'Madhav', 'Manoj', 'Meghana',
            'Nikhil', 'Pooja', 'Pranav', 'Rahul', 'Rohit', 'Varun'
        ),
        ' ',
        ELT(1 + ((seq.n * 5 + b.branch_id) % 16),
            'Reddy', 'Sharma', 'Verma', 'Rao', 'Kumar', 'Patel', 'Gupta', 'Naidu',
            'Chowdary', 'Singh', 'Yadav', 'Nair', 'Iyer', 'Prasad', 'Varma', 'Gowda'
        )
    ) AS name,
    b.branch_id,
    3 AS semester,
    CASE
        WHEN seq.n <= 60 THEN 'A'
        WHEN seq.n <= 120 THEN 'B'
        WHEN seq.n <= 180 THEN 'C'
        WHEN seq.n <= 240 THEN 'D'
        ELSE 'E'
    END AS section
FROM (
    SELECT branch_id, 'CSE'     AS prefix, 300 AS target_count FROM branches WHERE branch_code = 'CSE'
    UNION ALL
    SELECT branch_id, 'CSEAIML' AS prefix, 300 AS target_count FROM branches WHERE branch_code = 'CSE-AIML'
    UNION ALL
    SELECT branch_id, 'CSEDS'   AS prefix, 300 AS target_count FROM branches WHERE branch_code = 'CSE-DS'
    UNION ALL
    SELECT branch_id, 'CSEAIDS' AS prefix, 300 AS target_count FROM branches WHERE branch_code = 'CSE-AIDS'
    UNION ALL
    SELECT branch_id, 'CSECS'   AS prefix, 300 AS target_count FROM branches WHERE branch_code = 'CSE-CS'
    UNION ALL
    SELECT branch_id, 'ECE'     AS prefix, 100 AS target_count FROM branches WHERE branch_code = 'ECE'
    UNION ALL
    SELECT branch_id, 'EEE'     AS prefix, 100 AS target_count FROM branches WHERE branch_code = 'EEE'
    UNION ALL
    SELECT branch_id, 'MECH'    AS prefix, 100 AS target_count FROM branches WHERE branch_code = 'MECH'
) b
JOIN (
    SELECT (hundreds.d * 100 + tens.d * 10 + ones.d + 1) AS n
    FROM (SELECT 0 AS d UNION ALL SELECT 1 UNION ALL SELECT 2) hundreds
    CROSS JOIN (
        SELECT 0 AS d UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
        UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9
    ) tens
    CROSS JOIN (
        SELECT 0 AS d UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
        UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9
    ) ones
) seq
    ON seq.n <= b.target_count
ORDER BY b.branch_id, seq.n;

-- =========================================================================
-- STEP 3: Insert Controlled Marks for CSE001, CSE002, CSE003, CSE004 (DBMS ONLY)
--         Manish (CSE001) & Bipin Yadav (CSE002) only attended DBMS and scored in DBMS.
-- =========================================================================

-- 3A. CSE001 (Manish) -> DBMS only: Midterm 1 = 40, Midterm 2 = 35, Internals = 18, Final Exam = 80 (Total = 81.40 PASS)
INSERT INTO marks (student_id, assessment_id, marks_obtained)
SELECT
    st.student_id,
    a.assessment_id,
    CASE a.assessment_name
        WHEN 'Midterm 1'  THEN 40.00
        WHEN 'Midterm 2'  THEN 35.00
        WHEN 'Internals'  THEN 18.00
        WHEN 'Final Exam' THEN 80.00
    END AS marks_obtained
FROM student st
JOIN branch_subjects bs ON bs.branch_id = st.branch_id AND bs.semester = st.semester
JOIN subjects sub ON sub.subject_id = bs.subject_id
JOIN assessments a ON a.branch_subject_id = bs.branch_subject_id
WHERE st.roll_no = 'CSE001'
  AND sub.subject_code = 'DBMS'
ON DUPLICATE KEY UPDATE marks_obtained = VALUES(marks_obtained);

-- 3B. CSE002 (Bipin Yadav) -> DBMS only, missed Midterm 1 (no Midterm 1 row),
--     Midterm 2 = 35, Internals = 18, Final Exam = 70 (Total = 69.80 PASS)
INSERT INTO marks (student_id, assessment_id, marks_obtained)
SELECT
    st.student_id,
    a.assessment_id,
    CASE a.assessment_name
        WHEN 'Midterm 2'  THEN 35.00
        WHEN 'Internals'  THEN 18.00
        WHEN 'Final Exam' THEN 70.00
    END AS marks_obtained
FROM student st
JOIN branch_subjects bs ON bs.branch_id = st.branch_id AND bs.semester = st.semester
JOIN subjects sub ON sub.subject_id = bs.subject_id
JOIN assessments a ON a.branch_subject_id = bs.branch_subject_id
WHERE st.roll_no = 'CSE002'
  AND sub.subject_code = 'DBMS'
  AND a.assessment_name IN ('Midterm 2', 'Internals', 'Final Exam')
ON DUPLICATE KEY UPDATE marks_obtained = VALUES(marks_obtained);

-- 3C. CSE003 (Arman) -> DBMS only, skipped Internals (no Internals row) -> FAIL
INSERT INTO marks (student_id, assessment_id, marks_obtained)
SELECT
    st.student_id,
    a.assessment_id,
    CASE a.assessment_name
        WHEN 'Midterm 1'  THEN 42.00
        WHEN 'Midterm 2'  THEN 38.00
        WHEN 'Final Exam' THEN 76.00
    END AS marks_obtained
FROM student st
JOIN branch_subjects bs ON bs.branch_id = st.branch_id AND bs.semester = st.semester
JOIN subjects sub ON sub.subject_id = bs.subject_id
JOIN assessments a ON a.branch_subject_id = bs.branch_subject_id
WHERE st.roll_no = 'CSE003'
  AND sub.subject_code = 'DBMS'
  AND a.assessment_name IN ('Midterm 1', 'Midterm 2', 'Final Exam')
ON DUPLICATE KEY UPDATE marks_obtained = VALUES(marks_obtained);

-- 3D. CSE004 (Tharun) -> DBMS only, attended all 4 assessments but Final Exam = 35 (< 40) -> FAIL
INSERT INTO marks (student_id, assessment_id, marks_obtained)
SELECT
    st.student_id,
    a.assessment_id,
    CASE a.assessment_name
        WHEN 'Midterm 1'  THEN 45.00
        WHEN 'Midterm 2'  THEN 40.00
        WHEN 'Internals'  THEN 18.00
        WHEN 'Final Exam' THEN 35.00
    END AS marks_obtained
FROM student st
JOIN branch_subjects bs ON bs.branch_id = st.branch_id AND bs.semester = st.semester
JOIN subjects sub ON sub.subject_id = bs.subject_id
JOIN assessments a ON a.branch_subject_id = bs.branch_subject_id
WHERE st.roll_no = 'CSE004'
  AND sub.subject_code = 'DBMS'
ON DUPLICATE KEY UPDATE marks_obtained = VALUES(marks_obtained);

-- =========================================================================
-- STEP 4: Insert Marks for All Other 1,796 Students Across All 5 Subjects of Their Branch
--         Strictly matches student(branch_id, semester) = branch_subjects(branch_id, semester)
--         and respects max_marks (50, 50, 20, 100) and chk_marks_nonnegative.
-- =========================================================================
INSERT INTO marks (student_id, assessment_id, marks_obtained)
SELECT
    st.student_id,
    a.assessment_id,
    ROUND(
        CASE a.assessment_name
            WHEN 'Midterm 1' THEN
                CASE
                    WHEN (st.student_id % 20) IN (11, 19) THEN 12.00 + ((st.student_id * 3 + bs.branch_subject_id) % 11)
                    ELSE 28.00 + ((st.student_id * 7 + bs.branch_subject_id * 3) % 22)
                END
            WHEN 'Midterm 2' THEN
                CASE
                    WHEN (st.student_id % 20) IN (11, 19) THEN 14.00 + ((st.student_id * 5 + bs.branch_subject_id) % 10)
                    ELSE 29.00 + ((st.student_id * 9 + bs.branch_subject_id * 5) % 21)
                END
            WHEN 'Internals' THEN
                CASE
                    WHEN (st.student_id % 20) IN (11, 19) THEN 6.00 + ((st.student_id + bs.branch_subject_id) % 5)
                    ELSE 13.00 + ((st.student_id * 3 + bs.branch_subject_id) % 7)
                END
            WHEN 'Final Exam' THEN
                CASE
                    WHEN (st.student_id % 20) IN (11, 19) THEN 22.00 + ((st.student_id * 3 + bs.branch_subject_id) % 16)
                    ELSE 50.00 + ((st.student_id * 11 + bs.branch_subject_id * 7) % 48)
                END
        END,
        2
    ) AS marks_obtained
FROM student st
JOIN branch_subjects bs
    ON bs.branch_id = st.branch_id
    AND bs.semester = st.semester
JOIN assessments a
    ON a.branch_subject_id = bs.branch_subject_id
WHERE st.roll_no NOT IN ('CSE001', 'CSE002', 'CSE003', 'CSE004')
  AND NOT (
      -- ~5% missed Midterm 1 (counted as 0, still pass overall)
      (a.assessment_name = 'Midterm 1' AND (st.student_id % 20) = 4)
      OR
      -- ~5% missed Midterm 2 (counted as 0, still pass overall)
      (a.assessment_name = 'Midterm 2' AND (st.student_id % 20) = 8)
      OR
      -- ~5% skipped Internals -> automatic FAIL
      (a.assessment_name = 'Internals' AND (st.student_id % 20) = 15)
      OR
      -- ~5% skipped Final Exam -> automatic FAIL
      (a.assessment_name = 'Final Exam' AND (st.student_id % 20) = 0)
  )
ON DUPLICATE KEY UPDATE marks_obtained = VALUES(marks_obtained);

COMMIT;

USE student_performance_db;

UPDATE student
SET name = CONCAT(
    ELT(1 + ((student_id - 1) % 50),
        'Aarav', 'Vivaan', 'Aditya', 'Vihaan', 'Arjun', 'Reyansh', 'Ayaan', 'Krishna',
        'Ishaan', 'Shaurya', 'Atharv', 'Advik', 'Pranav', 'Dhruv', 'Kabir', 'Ritvik',
        'Aryan', 'Karthik', 'Yash', 'Ayush', 'Tanmay', 'Abhinav', 'Harsh', 'Siddharth',
        'Rohan', 'Aniket', 'Sahil', 'Kunal', 'Nikhil', 'Varun', 'Akash', 'Aditi',
        'Ananya', 'Aarohi', 'Diya', 'Anika', 'Navya', 'Riya', 'Pooja', 'Neha',
        'Kavya', 'Sanya', 'Isha', 'Radhika', 'Sneha', 'Tanvi', 'Megha', 'Swathi',
        'Vidya', 'Keerthi'
    ),
    ' ',
    ELT(1 + (FLOOR((student_id - 1) / 50) % 40),
        'Reddy', 'Sharma', 'Verma', 'Rao', 'Kumar', 'Patel', 'Gupta', 'Naidu',
        'Chowdary', 'Singh', 'Yadav', 'Nair', 'Iyer', 'Kaur', 'Prasad', 'Varma',
        'Babu', 'Murthy', 'Rajan', 'Gowda', 'Pillai', 'Das', 'Joshi', 'Menon',
        'Pande', 'Saxena', 'Mehra', 'Chopra', 'Malhotra', 'Bhat', 'Shenoy', 'Deshmukh',
        'Kulkarni', 'Pawar', 'Jadhav', 'Shinde', 'Ghosh', 'Roy', 'Sen', 'Banerjee'
    )
)
WHERE student_id > 4;