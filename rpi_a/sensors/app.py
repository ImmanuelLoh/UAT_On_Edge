from flask import Flask, render_template, redirect, url_for, request

app = Flask(__name__)

@app.route('/')
def page1():
    return render_template('page1.html')

@app.route('/task-color')
def page2():
    return render_template('page2.html')

@app.route('/task-selection', methods=['GET', 'POST'])
def page3():
    if request.method == 'POST':
        # Logic to check if exactly 3 are selected
        selected = request.form.getlist('options')
        if len(selected) == 3:
            return redirect(url_for('page4'))
    return render_template('page3.html')

@app.route('/complete')
def page4():
    return render_template('page4.html')

if __name__ == '__main__':
    app.run(debug=True)