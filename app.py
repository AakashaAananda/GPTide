from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import google.generativeai as genai
import json
from markupsafe import Markup

app = Flask(__name__)
app.secret_key = ''# Add any AI model API key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chatbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# AI model
genai.configure(api_key="")# Add any AI model API key
model = genai.GenerativeModel("gemini-1.5-flash")


db = SQLAlchemy(app)
# User Model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

# Query Model
class Query(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user_query = db.Column(db.Text, nullable=False)
    bot_response = db.Column(db.Text, nullable=False)

# Create Tables
with app.app_context():
    db.create_all()

# Routes
@app.route('/')
def home():
    return render_template('home.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if not username or not password:
            return "Username and password are required!", 400
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            return "Username already exists! Please choose another.", 400
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()

        return redirect(url_for('login'))

    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            return redirect(url_for('chat'))

        return "Invalid credentials!"

    return render_template('login.html')



def format_response(response_text):
    lines = response_text.split("\n")
    json_output = {
        "content": []
    }
    current_list = None

    for line in lines:
        if line.startswith("**") and line.endswith("**"): 
            json_output["content"].append({
                "type": "heading",
                "level": 3,
                "text": f"**{line.strip('*')}**"
            })
        elif line.startswith("* "):  
            if current_list is None:
                current_list = {"type": "list", "items": []}
                json_output["content"].append(current_list)
            current_list["items"].append(line[2:])
        else: 
            if current_list is not None:
                current_list = None
            if line.strip():  
                json_output["content"].append({
                    "type": "paragraph",
                    "text": line
                })

    return json.dumps(json_output)



def extract_and_print_content(formatted_response):
    data = json.loads(formatted_response)
    final_result = ""

    for item in data["content"]:
        if item["type"] == "paragraph":
            final_result += item["text"].strip() + "\n\n"
        elif item["type"] == "heading":
            final_result += f"{item['text'].strip()}\n\n"
        elif item["type"] == "list":
            for list_item in item["items"]:
                final_result += f"- {list_item.strip()}\n"
            final_result += "\n"

    final_result = final_result.strip()
    print(final_result)


@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        user_query = request.form['query']
        try:
            response = model.generate_content(user_query)
            bot_response_text = response
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                bot_response_text = candidate.content.parts[0].text
            formatted_response = format_response(bot_response_text)
            data = json.loads(formatted_response)

            final_result = ""
            for item in data["content"]:
                if item["type"] == "paragraph":
                    final_result += item["text"] + "\n"
                elif item["type"] == "list":
                    for list_item in item["items"]:
                        final_result += f"- {list_item}\n"

            final_result = final_result.strip()

            new_query = Query(user_id=session['user_id'], user_query=user_query, bot_response=formatted_response)
            db.session.add(new_query)
            db.session.commit()
            return render_template('chat.html', response=Markup(final_result))
        except Exception as e:
            return render_template('chat.html', error=f"Unexpected error: {str(e)}")

    return render_template('chat.html')



@app.route('/history')
def history():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_queries = Query.query.filter_by(user_id=session['user_id']).all()
    formatted_queries = []

    for query in user_queries:
        # Parse the JSON response
        bot_response_data = json.loads(query.bot_response)
        formatted_response = ""

        for item in bot_response_data["content"]:
            if item["type"] == "paragraph":
                formatted_response += item["text"] + "\n"
            elif item["type"] == "list":
                for list_item in item["items"]:
                    formatted_response += f"- {list_item}\n"

        formatted_response = formatted_response.strip()
        formatted_queries.append({
            'id': query.id,
            'user_query': query.user_query,
            'bot_response': formatted_response
        })

    return render_template('history.html', queries=formatted_queries)


@app.route('/edit/<int:query_id>', methods=['GET', 'POST'])
def edit(query_id):
    query = Query.query.get(query_id)
    if not query:
        return redirect(url_for('history', error="Query not found."))

    if request.method == 'POST':
        try:
            query.user_query = request.form['query']
            response = model.generate_content(query.user_query)
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                query.bot_response = candidate.content.parts[0].text
            else:
                query.bot_response = "No valid response from the bot."
            db.session.commit()
            return redirect(url_for('history'))
        except Exception as e:
            return render_template('edit.html', query=query, error=f"An error occurred: {str(e)}")
    return render_template('edit.html', query=query)


@app.route('/delete/<int:query_id>', methods=['POST'])
def delete(query_id):
    query = Query.query.get(query_id)
    db.session.delete(query)
    db.session.commit()
    return redirect(url_for('history'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)