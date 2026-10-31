
import getpass
import mysql.connector
from werkzeug.security import generate_password_hash

def main():
    username = input("Admin username: ").strip()
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")

    if not username or not password:
        print("Username and password cannot be empty.")
        return

    if password != confirm:
        print("Passwords do not match.")
        return

    if len(password) < 8:
        print("Password must contain at least 8 characters.")
        return

    mysql_password = getpass.getpass("MySQL root password: ")

    connection = mysql.connector.connect(
        host="localhost",
        user="root",
        password=mysql_password,
        database="student_performance_db"
    )

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO users (username, password_hash, role, is_active)
            VALUES (%s, %s, 'admin', TRUE)
            """,
            (username, generate_password_hash(password))
        )
        connection.commit()
        print("Admin account created successfully.")

    except mysql.connector.IntegrityError:
        connection.rollback()
        print("Username already exists. Choose another.")

    finally:
        cursor.close()
        connection.close()

if __name__ == "__main__":
    main()
