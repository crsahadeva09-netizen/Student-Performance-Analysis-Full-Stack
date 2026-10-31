# Student Performance Analysis System

A full-stack academic management and analytics platform designed to manage student records, track attendance, analyze academic performance, and generate meaningful insights from student assessment data.

## Overview

The **Student Performance Analysis System** helps educational institutions organize academic information and evaluate student performance through a centralized web application.

The platform combines student management, attendance tracking, marks management, and data-driven performance analytics to support better academic monitoring and decision-making.

## Key Features

- **Student Management:** Manage student records and academic information.
- **Attendance Management:** Record and monitor student attendance.
- **Assessment Management:** Create and manage academic assessments.
- **Marks Management:** Maintain student assessment marks.
- **Performance Analysis:** Calculate and analyze academic performance.
- **Academic Analytics:** View student performance trends and analytical summaries.
- **Subject Management:** Organize academic subjects and related information.
- **Authentication:** Secure access to application functionality.

## Technology Stack

| Component | Technologies |
|---|---|
| Frontend | HTML5, CSS3, JavaScript |
| Backend | Python, Flask |
| Database | MySQL |
| API Architecture | REST APIs |
| Testing | Pytest |
| Version Control | Git, GitHub |

## Project Structure

```text
Student-Performance-Analysis-Full-Stack/
│
├── backend/
│   ├── app.py
│   ├── routes/
│   ├── migrations/
│   ├── tests/
│   ├── database.py
│   ├── performance_calculator.py
│   └── requirements.txt
│
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── script.js
│
├── database/
│   └── python_project.sql
│
├── ATTENDANCE_SETUP.md
├── README.md
└── .gitignore
```

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/crsahadeva09-netizen/Student-Performance-Analysis-Full-Stack.git
```

### 2. Navigate to the Project

```bash
cd Student-Performance-Analysis-Full-Stack
```

### 3. Set Up the Python Environment

```bash
cd backend

python -m venv .venv
```

Activate the environment on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

### 4. Configure the Database

- Install and configure MySQL.
- Create the application database.
- Import the SQL script located at `database/python_project.sql`.
- Configure database connection details using environment variables.
- Configure the Flask secret key using an environment variable.

Never commit actual database passwords or secret keys.

### 5. Run the Application

Configure the backend and frontend according to the application's existing Flask configuration and API setup.

Refer to the source code and `ATTENDANCE_SETUP.md` for additional configuration details.

## Testing

The backend includes automated tests covering API functionality, authentication integration, performance analytics, and performance calculations.

Run the test suite from the backend directory:

```bash
pytest -v
```

## Database

The project includes a MySQL database initialization script:

`database/python_project.sql`

Additional database migrations are maintained under:

`backend/migrations/`

## Security

- Keep database credentials outside source code.
- Store secret keys in environment variables.
- Never commit `.env` files.
- Use `.env.example` for sharing configuration requirements without exposing secrets.

## Future Enhancements

- Advanced academic performance visualizations.
- Enhanced reporting and export functionality.
- Improved mobile responsiveness.
- Additional academic insights and performance indicators.

## Contributors

Developed collaboratively as a full-stack academic software project.

## License

No license has been assigned to this project.

All rights reserved. No permission is granted to use, copy, modify, or distribute this code without prior authorization from the copyright holders.

---

**Repository:** [Student Performance Analysis – Full Stack](https://github.com/crsahadeva09-netizen/Student-Performance-Analysis-Full-Stack)