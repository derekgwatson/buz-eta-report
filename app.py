import pandas as pd
import requests
import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, jsonify
import secrets
import urllib.parse

app = Flask(__name__)

# Database file path
DB_PATH = 'customers.db'


# Function to initialize the database
def init_db():
    if not os.path.exists(DB_PATH):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE customers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    obfuscated_id TEXT NOT NULL UNIQUE,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL
                )
            ''')
            conn.commit()
            print("Database and table created successfully")


# Helper function to interact with the database
def query_db(query, args=(), one=False):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall()
        conn.commit()
        return (rv[0] if rv else None) if one else rv


# Initialize the database when the app starts
init_db()


@app.route('/')
def home():
    return "Welcome to the Reporting System"


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        name = request.form['name']
        url = request.form['url']
        title = request.form['title']
        obfuscated_id = secrets.token_urlsafe(8)  # Generate a unique obfuscated ID

        # Insert customer into the database
        query_db(
            "INSERT INTO customers (name, obfuscated_id, url, title) VALUES (?, ?, ?, ?)",
            (name, obfuscated_id, url, title),
        )
        return redirect(url_for('admin'))

    # Retrieve all customers from the database
    customers = query_db("SELECT id, name, obfuscated_id, url, title FROM customers")
    return render_template('admin.html', customers=customers)


@app.route('/reports/<obfuscated_id>')
def show_report(obfuscated_id):
    customer = query_db(
        "SELECT name, url, title FROM customers WHERE obfuscated_id = ?", (obfuscated_id,), one=True
    )
    if not customer:
        return "Report not found", 404

    name, url, title = customer
    return render_template('report.html', customer_url=url, customer_title=title)


@app.route('/delete/<int:customer_id>')
def delete_customer(customer_id):
    query_db("DELETE FROM customers WHERE id = ?", (customer_id,))
    return redirect(url_for('admin'))


@app.route('/edit/<int:customer_id>', methods=['GET', 'POST'])
def edit_customer(customer_id):
    if request.method == 'POST':
        # Fetch updated form data
        name = request.form['name']
        url = request.form['url']
        title = request.form['title']

        # Update the customer in the database
        query_db(
            "UPDATE customers SET name = ?, url = ?, title = ? WHERE id = ?",
            (name, url, title, customer_id),
        )
        return redirect(url_for('admin'))

    # Fetch customer details for pre-filling the form
    customer = query_db(
        "SELECT id, name, url, title FROM customers WHERE id = ?", (customer_id,), one=True
    )
    if not customer:
        return "Customer not found", 404

    return render_template('edit.html', customer=customer)


@app.route('/sales-report')
def sales_report():
    # Base URL for SalesReport
    root_url = "http://api.buzmanager.com/reports/DESDR"
    sales_report_url = f"{root_url}/SalesReport"

    # Step 1: Build the OData filter
    # Filters: Workflow_Job_Tracking_Status not in ("Completed", "Cancelled") and Order_Status == "Work in Progress"
    filter_conditions = [
        "Workflow_Job_Tracking_Status ne 'Completed'",
        "Workflow_Job_Tracking_Status ne 'Cancelled'",
        "Order_Status eq 'Work in Progress'"
    ]
    odata_filter = " and ".join(filter_conditions)
    encoded_filter = urllib.parse.quote(odata_filter)  # Encode the filter for the URL
    filtered_sales_report_url = f"{sales_report_url}?$filter={encoded_filter}"

    # Step 2: Fetch filtered SalesReport data
    username = os.getenv("BUZMANAGER_USERNAME")
    password = os.getenv("BUZMANAGER_PASSWORD")
    response = requests.get(filtered_sales_report_url, auth=(username, password))
    response.raise_for_status()
    sales_report_data = response.json()

    # Step 3: Convert SalesReport data to Pandas DataFrame
    _sales_report = pd.DataFrame(sales_report_data.get("value", []))
    print(sales_report_data)

    # Step 4: Filter by Customer (if query parameter provided)
    customer = request.args.get("customer")  # Get 'customer' from query params
    if customer:
        _sales_report = _sales_report[_sales_report["Customer"] == customer]

    # Convert the DataFrame to JSON and return it
    return jsonify(_sales_report.to_dict(orient="records"))


if __name__ == '__main__':
    app.run(debug=True)
