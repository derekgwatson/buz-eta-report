import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, jsonify
import secrets
from services.buz_data import get_open_orders, get_schedule_jobs_details

app = Flask(__name__)

# Database file path
DB_PATH = 'customers.db'


# Function to initialize the database
def init_db():
    try:
        if not os.path.exists(DB_PATH):
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute('''
                    CREATE TABLE customers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        dd_name TEXT,
                        cbr_name TEXT,
                        obfuscated_id TEXT NOT NULL UNIQUE
                    )
                ''')
                conn.commit()
                print("Database and table created successfully")
    except Exception as e:
        print(f"Error initializing database: {e}")
        raise


# Helper function to interact with the database
def query_db(query, args=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall()
        conn.commit()
        return (rv[0] if rv else None) if one else rv


@app.route('/')
def home():
    return "Welcome to the Reporting System"


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        dd_name = request.form['dd_name']
        cbr_name = request.form['cbr_name']
        obfuscated_id = secrets.token_urlsafe(8)

        # Insert customer into the database
        query_db(
            "INSERT INTO customers (dd_name, cbr_name, obfuscated_id) VALUES (?, ?, ?)",
            (dd_name, cbr_name, obfuscated_id),
        )
        return redirect(url_for('admin'))

    # Retrieve all customers from the database
    customers = query_db("SELECT id, dd_name, cbr_name, obfuscated_id FROM customers")
    return render_template('admin.html', customers=customers)


@app.route('/reports/<obfuscated_id>')
def show_report(obfuscated_id):
    customer = query_db("SELECT dd_name, cbr_name FROM customers WHERE obfuscated_id = ?", (obfuscated_id,), one=True)
    if not customer:
        return "Report not found", 404

    try:
        # Fetch data for both instances
        data_dd = get_open_orders(customer[0], "DD")
        data_cbr = get_open_orders(customer[1], "CBR")

        # Combine the results
        combined_data = data_cbr + data_dd
        return render_template('report_v2.html', data=combined_data)
    except Exception as e:
        return f"Error generating sales report: {e}", 500


@app.route('/delete/<int:customer_id>')
def delete_customer(customer_id):
    query_db("DELETE FROM customers WHERE id = ?", (customer_id,))
    return redirect(url_for('admin'))


@app.route('/edit/<int:customer_id>', methods=['GET', 'POST'])
def edit_customer(customer_id):
    if request.method == 'POST':
        # Fetch updated form data
        dd_name = request.form['dd_name']
        cbr_name = request.form['cbr_name']

        # Update the customer in the database
        query_db(
            "UPDATE customers SET cbr_name = ?, dd_name = ? WHERE id = ?",
            (cbr_name, dd_name, customer_id),
        )
        return redirect(url_for('admin'))

    # Fetch customer details for pre-filling the form
    customer = query_db(
        "SELECT id, dd_name, cbr_name FROM customers WHERE id = ?", (customer_id,), one=True
    )
    if not customer:
        return "Customer not found", 404

    return render_template('edit.html', customer=customer)


@app.route('/jobs-schedule/<order_no>', methods=['GET'])
def jobs_schedule(order_no):
    """Route to fetch JobsScheduleDetails for a given order number."""
    data = get_schedule_jobs_details(order_no, "JobsScheduleDetailed", "DD")
    return jsonify(data)


@app.route('/wip/<order_no>', methods=['GET'])
def work_in_progress(order_no):
    """Route to fetch WorkInProgress for a given order number."""
    data = get_schedule_jobs_details(order_no, "WorkInProgress")
    return jsonify(data)


if __name__ == '__main__':
    # Initialize the database when the app starts
    init_db()
    app.run(debug=True)
