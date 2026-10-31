
import getpass
import mysql.connector
from werkzeug.security import generate_password_hash

password = getpass.getpass("Enter your new login password: ")
confirm = getpass.getpass("Confirm new password: ")

if len(password) < 8 or password != confirm:
    raise SystemExit("Passwords must match and be at least 8 characters.")

connection = mysql.connector.connect(
    host="localhost",
    user="root",
    password=getpass.getpass("Enter your MySQL root password: "),
    database="student_performance_db"
)

try:
    cursor = connection.cursor()
    cursor.execute(
        """
        UPDATE users
        SET password_hash = %s, is_active = TRUE
        WHERE username = %s
        """,
        (generate_password_hash(password), "root_person")
    )
    connection.commit()

    if cursor.rowcount:
        print("Password updated successfully for root_person.")
    else:
        print("No account updated. Check the username.")

    cursor.close()
finally:
    connection.close()
