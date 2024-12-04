import re
from flask import Flask, jsonify, render_template, session, request, redirect, url_for, render_template_string
from sqlalchemy import create_engine
import sqlalchemy as sa
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt 
from matplotlib.ticker import MaxNLocator
import matplotlib
matplotlib.use('agg')

engine = create_engine('mysql+pymysql://root:123456@35.225.101.115:3306/Olympics?charset=utf8mb4')
conn = engine.connect()

app = Flask(__name__)

app.secret_key = 'team18stage4'  # Replace with a strong secret key

@app.route('/',methods=['GET','POST'])
def index():

    user_logged_in = 'username' in session
    if 'username' in session:
        username = session['username']
        return render_template('index.html', user_logged_in=user_logged_in, user_name  = username)

    
    return render_template('index.html',user_name  = "")

@app.route('/login',methods=['GET','POST'])
def login():
    # User login.
    if request.method == 'POST':
        find_user_query = '''SELECT Password from User WHERE UserName = '''
        user_name = request.form['username']
        password = request.form['password']
        session['username'] = user_name
        find_user_query += "'" + session['username'] + "'"
       
        user_df = pd.read_sql(find_user_query, engine)
        print('login user: ' + session['username'])
        # if it is a existed user.
        if (len(user_df) == 1):
            stored_password = user_df['Password'].iloc[0]
            print(user_df['Password'].iloc[0])
            if (password != stored_password):
                return 'Invalid login password. <a href="/login">Try again</a>'
        # if it is a new user
        
        else:
            #TODO: add new user to the database?
            insert_user_query = f'INSERT INTO Olympics.User (UserName, Password) VALUES ("{user_name}", "{password}")'
            conn.execute(sa.text(insert_user_query))
            conn.commit()
            print("new user!")

        return redirect(url_for('index'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))

@app.route('/search', methods=['GET','POST'])
def search():
    if request.method == 'POST':
        # Get result from drop down list.
        selected_country = request.form.get('selected_option')
        country_info_query = f'SELECT * FROM Olympics.Country WHERE CountryName = "{selected_country}"'
        discipline_groupby_query = f'SELECT Discipline, COUNT(Discipline) AS count FROM Olympics.Athlete WHERE CountryName = "{selected_country}" Group BY Discipline;'
        selected_country_df =  pd.read_sql(sa.text(country_info_query), engine)
        discipline_groupby_df = pd.read_sql(sa.text(discipline_groupby_query), engine)
        discipline_json = discipline_groupby_df.to_dict('records');
        print(discipline_groupby_df)
        print(discipline_json)
        gold = selected_country_df["Gold"].iloc[0]
        silver = selected_country_df["Silver"].iloc[0]
        bronze = selected_country_df["Bronze"].iloc[0]
        total_medals = selected_country_df["Total"].iloc[0]
        
        # display the country medals info
        content = {
            'display1': f'Total medals: {total_medals}',
            'display2': f'Gold: {gold}',
            'display3': f'Silver: {silver}',
            'display4': f'Bronze: {bronze}',
            'Discipline_info': discipline_json
        }
        return jsonify(content)
    return render_template('search.html')

@app.route('/get_dropdown_options')
def get_dropdown_options():

    # Get user input from text box
    user_input = request.args.get('input', '')
    user_options = []
    
    
    if (user_input == ''):
        query = '''SELECT CountryName FROM Olympics.Country'''
        all_options_df =  pd.read_sql(query, engine)
        for i in range(len(all_options_df)):
            user_options.append(all_options_df["CountryName"].iloc[i])
    else:
        # Get country name started with user entered value.
        query = '''SELECT CountryName FROM Olympics.Country WHERE CountryName LIKE '''
        user_input = "%" + user_input + "%"
        
        query = f'SELECT CountryName FROM Olympics.Country WHERE CountryName LIKE "{user_input}"'
        print(query)
        
        filtered_options_df =  pd.read_sql(sa.text(query), engine)
        for i in range(len(filtered_options_df)):
            user_options.append(filtered_options_df["CountryName"].iloc[i])

    return jsonify({'options': user_options})

@app.route('/compare', methods=['GET', 'POST'])
def compare():
    first_country = session.get('first_selected_country')
    print(first_country)
    countries = []
    query = '''SELECT CountryName FROM Olympics.Country'''
    all_options_df =  pd.read_sql(query, engine)
    for i in range(len(all_options_df)):
        countries.append(all_options_df["CountryName"].iloc[i])
    methods = ["Medals Information", "Coach & Athlete", "Disciplines"]
    first_info = {}
    second_info = {}
    if request.method == 'POST':
        second_country = request.form.get('second_country')
        selected_method = request.form.get('selected_method')
        print(selected_method)
        countries = [first_country,second_country]

        if selected_method == "Medals Information":
            first_info = get_country_medals_info(first_country)
            second_info = get_country_medals_info(second_country)
        elif selected_method == "Coach & Athlete":
            first_info = get_country_people(first_country)
            second_info = get_country_people(second_country)
        elif selected_method == "Disciplines":
            first_info = get_country_disciplines(first_country)
            second_info = get_country_disciplines(second_country)
        print(first_info)
        print(second_info)
        
        return jsonify({'countries': countries, 'first_country_info': first_info, 'second_country_info': second_info}) 
    
    return render_template('compare.html',  countries=countries, first_country = first_country, methods = methods)
    
# get number of diciplines.
def get_country_disciplines(countryName):
    query = f'SELECT COUNT(Distinct Discipline) AS num_disciplines FROM Olympics.Athlete WHERE CountryName = "{countryName}"'
    country_info = pd.read_sql(sa.text(query), engine)
    num_disciplines = country_info["num_disciplines"].iloc[0]
    return {
        'num_disciplines': int(num_disciplines)
    }

# return country medals
def get_country_medals_info(countryName):
    query = f'SELECT * FROM Olympics.Country WHERE CountryName = "{countryName}"'
    country_info = pd.read_sql(sa.text(query), engine)
    gold = country_info["Gold"].iloc[0]
    silver = country_info["Silver"].iloc[0]
    bronze = country_info["Bronze"].iloc[0]
    total_medals = country_info["Total"].iloc[0]
    return {
        'gold': int(gold),  
        'silver': int(silver),
        'bronze': int(bronze),
        'total': int(total_medals)
    }
# return country coaches and athletes numbers.
def get_country_people(countryName):
    query_total_coaches = f'SELECT COUNT(*) AS total_coaches FROM Olympics.Coach WHERE CountryName = "{countryName}"'
    query_total_athletes = f'SELECT COUNT(*) AS total_athletes FROM Olympics.Athlete WHERE CountryName = "{countryName}"'
    coaches_info = pd.read_sql(sa.text(query_total_coaches), engine)
    athletes_info = pd.read_sql(sa.text(query_total_athletes), engine)
    total_coaches = coaches_info["total_coaches"].iloc[0]
    total_athletes = athletes_info["total_athletes"].iloc[0]
    return {
        'total_coaches': int(total_coaches),
        'total_athletes': int(total_athletes)
    }

# After Insert Trigger Stored in MySql WorkBench for like function
# CREATE DEFINER=`root`@`%` TRIGGER `Like_AFTER_INSERT` AFTER INSERT ON `Like` FOR EACH ROW BEGIN
#    DECLARE avg_goldc FLOAT;
#    DECLARE avg_silverc FLOAT;
#    DECLARE avg_bronzec FLOAT;
#    DECLARE avg_totalc FLOAT;
#    DECLARE country_count INT;

#    SELECT AVG(Gold), AVG(Silver), AVG(Bronze), AVG(Total), COUNT(CountryName)
#    INTO avg_goldc, avg_silverc, avg_bronzec, avg_totalc, country_count
#    FROM Olympics.Like
#    WHERE UserName = NEW.UserName;

#    IF EXISTS (SELECT 1 FROM Olympics.Like_Stat WHERE UserName = NEW.UserName) THEN
#        UPDATE Olympics.Like_Stat
#        SET Avg_Gold = avg_goldc,
#            Avg_Silver = avg_silverc,
#            Avg_Bronze = avg_bronzec,
#            Avg_Total = avg_totalc,
#            Total_Liked = country_count
#        WHERE UserName = NEW.UserName;
#    ELSE
#        INSERT INTO Olympics.Like_Stat (UserName, Avg_Gold, Avg_Silver, Avg_Bronze, Avg_Total, Total_Liked)
#        VALUES (NEW.UserName, avg_goldc, avg_silverc, avg_bronzec, avg_totalc, country_count);
#    END IF;
# END
@app.route('/like_store', methods=['POST'])
def like_option():
    if 'username' not in session:
        return jsonify({'status': 'logged_in_request'})
    user_name = session['username']
    selected_country = request.form.get('selected_like_country')
    curr_timestamp = datetime.strftime(datetime.now(), "%Y-%m-%d %H:%M:%S")
    medals_info = get_country_medals_info(selected_country)
    print(user_name, " liked: ", selected_country) 
    search_query = f'SELECT UserName, CountryName FROM Olympics.Like WHERE CountryName = "{selected_country}" AND UserName = "{user_name}"'
    exist_like_df = pd.read_sql(sa.text(search_query), engine)
    query = ''
    if (len(exist_like_df) == 0):
        query = f'''INSERT IGNORE INTO Olympics.Like
                        (UserName, CountryName, TimeStamp, Gold, Silver, Bronze, Total) 
                        VALUES ("{user_name}", "{selected_country}", "{curr_timestamp}", 
                                {medals_info['gold']}, {medals_info['silver']}, {medals_info['bronze']}, {medals_info['total']})'''

        # need to commit after insert.
        
    else:
        query = f'UPDATE Olympics.Like SET TimeStamp = "{curr_timestamp}" WHERE CountryName = "{selected_country}" AND UserName = "{user_name}" '
    
    conn.execute(sa.text(query))
    conn.commit();
    

    return jsonify({'status': 'success'})

# After Delete Trigger Stored in MySql WorkBench for dislike function
# CREATE DEFINER=`root`@`%` TRIGGER `Like_AFTER_DELETE` AFTER DELETE ON `Like` FOR EACH ROW BEGIN
#    DECLARE avg_goldc FLOAT;
#    DECLARE avg_silverc FLOAT;
#    DECLARE avg_bronzec FLOAT;
#    DECLARE avg_totalc FLOAT;
#    DECLARE country_count INT;

#    SELECT AVG(Gold), AVG(Silver), AVG(Bronze), AVG(Total), COUNT(CountryName)
#    INTO avg_goldc, avg_silverc, avg_bronzec, avg_totalc, country_count
#    FROM Olympics.Like
#    WHERE UserName = OLD.UserName;

#    IF EXISTS (SELECT 1 FROM Olympics.Like_Stat WHERE UserName = OLD.UserName) THEN
#        UPDATE Olympics.Like_Stat
#        SET Avg_Gold = avg_goldc,
#            Avg_Silver = avg_silverc,
#            Avg_Bronze = avg_bronzec,
#            Avg_Total = avg_totalc,
#            Total_Liked = country_count
#        WHERE UserName = OLD.UserName;
#    ELSE
#        INSERT INTO Olympics.Like_Stat (UserName, Avg_Gold, Avg_Silver, Avg_Bronze, Avg_Total, Total_Liked)
#        VALUES (OLD.UserName, avg_goldc, avg_silverc, avg_bronzec, avg_totalc, country_count);
#    END IF;
# END
@app.route('/dislike_store', methods=['POST'])
def dislike_option():
    if 'username' not in session:
        return jsonify({'status': 'logged_in_request'})
    user_name = session['username']
    selected_country = request.form.get('selected_dislike_country')
    
    print(user_name, " disliked: ", selected_country) 
    
    query = f'DELETE FROM Olympics.Like WHERE UserName = "{user_name}" AND CountryName = "{selected_country}"'

    # need to commit after insert.
    conn.execute(sa.text(query))
    conn.commit();

    return jsonify({'status': 'success'})

@app.route('/first_country_compare_store', methods=['POST'])
def like_store():
    session['first_selected_country'] = request.form.get('first_selected_country')
    return jsonify({'status': 'success'})


@app.route('/like', methods=['GET'])
def like():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    # Retrieve Countries the current User liked
    query_likes = '''SELECT CountryName, Gold, Silver, Bronze, Total, TimeStamp 
                     FROM Olympics.Like 
                     WHERE UserName = :username'''
    liked_countries_df = pd.read_sql(sa.text(query_likes), engine, params={'username': username})

    # Retrieve Like Statistics of the current User
    query_stats = '''SELECT Avg_Gold, Avg_Silver, Avg_Bronze, Avg_Total, Total_Liked 
                     FROM Olympics.Like_Stat 
                     WHERE UserName = :username'''
    
    like_stats_df = pd.read_sql(sa.text(query_stats), engine, params={'username': username})
    
    if len(like_stats_df) == 0:
        liked_countries = {}
        like_stats = {}
    else:
        
        liked_countries = liked_countries_df.to_dict('records')
        like_stats = like_stats_df.iloc[0].to_dict()

    return render_template('like.html', username=username, liked_countries=liked_countries, like_stats=like_stats)


# This is the procedure stored in sql workbench:
# CREATE DEFINER=`root`@`%` PROCEDURE `advQue`()
# BEGIN

#   DECLARE finished INT DEFAULT 0;
#   DECLARE coach_name VARCHAR(255);
#   DECLARE athlete_count INT;
#   DECLARE total_athlete INT;
#   DECLARE country_name VARCHAR(255);
#   DECLARE total_medals INT;
  
#   DECLARE cursorOne CURSOR FOR 
#     SELECT c.name, COUNT(a.name)
#     FROM Olympics.Athlete AS a 
#     JOIN Olympics.Coach AS c ON a.CountryName = c.CountryName AND a.Discipline = c.Discipline
#     GROUP BY c.name
#     ORDER BY c.name;

#   DECLARE cursorTwo CURSOR FOR 
#     SELECT COUNT(a.Name), a.CountryName, c.Total
#     FROM Olympics.Athlete a 
#     JOIN Olympics.Country c ON c.CountryName = a.CountryName
#     GROUP BY a.CountryName
#     ORDER BY c.Total;

#   DECLARE CONTINUE HANDLER FOR NOT FOUND SET finished = 1;
  
#   DROP TEMPORARY TABLE IF EXISTS TempCoachInfo;
#   DROP TEMPORARY TABLE IF EXISTS TempCountryInfo;
  
#   CREATE TEMPORARY TABLE IF NOT EXISTS TempCoachInfo (
#     coach_name VARCHAR(255),
#     athlete_count INT
#   );
  
#   CREATE TEMPORARY TABLE IF NOT EXISTS TempCountryInfo (
#     total_athlete INT,
#     country_name VARCHAR(255),
#     total_medals INT
#   );

#   OPEN cursorOne;

#   coach_loop: LOOP
#     FETCH cursorOne INTO coach_name, athlete_count;
#     IF finished = 1 THEN 
#       LEAVE coach_loop;
#     END IF;

#     INSERT INTO TempCoachInfo (coach_name, athlete_count) VALUES (coach_name, athlete_count);
#   END LOOP;
#   CLOSE cursorOne;
  
#   SET finished = 0;
#   OPEN cursorTwo;

#   country_loop: LOOP
#     FETCH cursorTwo INTO total_athlete, country_name, total_medals;
#     IF finished = 1 THEN 
#       LEAVE country_loop;
#     END IF;
    
#     INSERT INTO TempCountryInfo (total_athlete, country_name, total_medals) VALUES (total_athlete, country_name, total_medals);
#   END LOOP;
#   CLOSE cursorTwo;
  
#   SELECT * FROM TempCoachInfo;
#   SELECT * FROM TempCountryInfo;
  
#   DROP TEMPORARY TABLE IF EXISTS TempCoachInfo;
#   DROP TEMPORARY TABLE IF EXISTS TempCountryInfo;

# END
@app.route('/advInfo', methods=['GET'])
def advInfo():
    try:
        cursor = engine.connect().connection.cursor()
        cursor.callproc('advQue')
        coach_info = cursor.fetchall()
        cursor.nextset()
        country_info = cursor.fetchall()
        cursor.close()
        # print("Coach Info:", coach_info)
        # print("Country Info:", country_info)
        return render_template('advInfo.html', coach_info=coach_info, country_info=country_info)
    except Exception as e:
        return jsonify({'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True)
