import re
import os
import time
import json
import telebot
import logging
import requests
import psycopg2
import datefinder
import numpy as np
import onnxruntime
from datetime import datetime
from datetime import timedelta
from tsresolve import point_of_time
from telebot import types

# Import utility class
from core.event import Event
from core.goblin import Goblin

class TeleBot: 
    '''
    This is the class which initializes a telegram bot
    '''

    def __init__(self, bot_token,  encryption_key, nlp, model):
        '''
        The constructor for TeleBot class

        Parameters:
        bot_token (string) : The secret token for Telegram bot
        parser_url (string) : The API endpoint for calling Rasa

        Returns:
        None
        '''

        # Initialize bot
        self.bot = telebot.TeleBot(token=bot_token, threaded=False)

        # Set the inference model
        self.nlp = nlp        
        self.model = model

        # Initialize Goblin
        self.goblin = Goblin(bytes(encryption_key, encoding='utf-8'))
        logging.info("Goblin initialized")

        self.markup = self.gen_markup()

        # Initialize a dictionary
        self.bricks = {}
    
    def activate(self):
        '''
        This function activates the bot and listens to messages

        Parameters:
        None

        Returns:
        None
        '''       
        
        @self.bot.callback_query_handler(func=lambda call: True)
        def callback_query(call):
            logging.info("Callback triggered")
            
            if call.data == "cb_yes":
                # Access the message
  
                self.process_feedback(call.message.chat.id, True)

            elif call.data == "cb_no":
                #self.bot.answer_callback_query(call.id, "Answer is No")
                self.process_feedback(call.message.chat.id, False)

            elif call.data == "store":

                try:
                    # Make details valid 
                    self.bricks[call.message.chat.id]['event'].make_valid()
                    

                    # Then call form action
                    self.form_action(call.message.chat.id)

                except Exception as error:
                    logging.info(error)   

            elif call.data == "storent":

                try:
                    # Ignore and move to next message
                    self.send_tracked_message(call.message.chat.id)
                except Exception as error:
                    logging.info(error)

            elif call.data == "edit":

                try:
                    # Edit message to show menu
                    self.show_entity_menu(call.message.chat.id)
                except Exception as error:
                    logging.info(error)

            elif call.data == "time":

                try:

                    # Delete exisiting time 
                    self.bricks[call.message.chat.id]['event'].delete_entity('time')

                    # Call form action
                    self.form_action(call.message.chat.id)
                except Exception as error:
                    logging.info(error)

            elif call.data == "date":

                try:
                    # Delete exisiting time 
                    self.bricks[call.message.chat.id]['event'].delete_entity('date')

                    # Call form action
                    self.form_action(call.message.chat.id)

                except Exception as error:
                    logging.info(error)

            elif call.data == "back":

                try:
                    # Go back to prev menu
                    chat_id = call.message.chat.id
                    message_id = self.bricks[chat_id]['menu_msg']['id']
                    text = self.bricks[chat_id]['menu_msg']['text']
                    
                    markup = self.correctness_markup()

                    self.bot.edit_message_text(chat_id=chat_id,message_id=message_id,
                                        text=text, reply_markup=markup,
                                        parse_mode="Markdown")
                except Exception as error:
                    logging.info(error)

        @self.bot.message_handler(commands=['start'])
        def send_welcome(message):
            '''
            This function is used to test if bot is active

            Parameters:
            message (dictionary) : Message object returned by telegram

            Return:
            None
            '''
            
            try:

                self.bot.reply_to(message, 'Bot is active and listening')

            except Exception as error:
                logging.info(repr(error))

        @self.bot.message_handler(commands=['remind'])
        def show_stored_messages(message):
            '''
            This function shows the messages stored
            in the database
            Parameters:
            message (dictionary) : Message object returned by telegram

            Return:
            None
            '''

            try:
                # Send a confirmation that command was received
                self.bot.reply_to(message, "Brb with your reminders..")

                # Retrieve and send all the message
                self.send_stored_messages(message.chat.id)

            except Exception as error:
                logging.info(repr(error))

        @self.bot.message_handler(commands=['help'])
        def help(message):
            '''
            This function shows help message
            Parameters:
            message (dictionary) : Message object returned by telegram
            Return:
            None
            '''

            # Send help message
            self.send_help_message(message.chat.id)

        
        @self.bot.message_handler(commands=['show'])
        def show_messages(message):
            '''
            This function is used to test if bot is active

            Parameters:
            message (dictionary) : Message object returned by telegram

            Return:
            None
            '''
            
            self.bot.reply_to(message, 'Brb with your messages')

            
            # Remove previously allocated brick
            if message.chat.id in self.bricks:
                del self.bricks[message.chat.id]
            
            # Allocate a brick to chat
            if message.chat.id not in self.bricks:

                try:
                    
                    # Get tracked messages
                    tracker = self.retrieve_tracker(message.chat.id)
                    if len(tracker) == 0:
                        self.bot.send_message(message.chat.id, "No imp messages were detected")
                        raise ValueError("Tracker is empty")
                    
                    self.bricks[message.chat.id] = self.generate_brick()

                    # Populate the brick with relevant data

                    # Get the generator object
                    gen_event = self.get_event_generator(tracker)
                    self.bricks[message.chat.id]['gen_event'] = gen_event

                    # List tracked events
                    self.send_tracked_message(message.chat.id)

                except ValueError as error:
                    logging.info(error)
                    #self.bot.send_message(message.chat.id, "No imp messages were detected")
                
                except Exception as error:
                    logging.info(error)
                

        
        @self.bot.message_handler(func = lambda message : True)
        def track_messages(message):

            if message.reply_to_message is None:
                if self.is_event_notification(message.text):

                    # Message is an event
                    # Store it!

                    logging.info("Event detected")
                    self.store_message(message)
            else:
                # Message is possibly a reponse to bot
                
                
                # Check if a brick has been allocated to this chat
                # Note that a brick is only allocated when the bot
                # Is interacting , and after interaction 
                # The brick is destroyed
                if message.chat.id in self.bricks:
                    
                    # Check if reply is being given to correct query
                    condition = message.reply_to_message.message_id == self.bricks[message.chat.id]['req_id']

                    if condition :
                        
                        # Retrieve the event being processed
                        event = self.bricks[message.chat.id]['event']

                        # Check what was the entity being requested
                        entity = event.get_req_entity()

                        # There will be multiple tiers of information extraction
                        # although no such extraction needed for desc

                        if entity == 'description':
                            event.add_event_detail('description', message.text)

                        else:
                    
                            # Initially no entities have been extracted
                            entity_extracted = False

                            if entity == 'date':
                                date_object = self.extract_date(message.text)
                                if date_object is not None:
                                    entity_extracted = True
                                    date_string = self.get_date_string(date_object)
                                    event.add_event_detail(entity, date_string)
                            
                            elif entity  == 'time':
                                time_string = self.extract_time(message.text)
                                
                                if time_string is not None:
                                    entity_extracted = True
                                    event.add_event_detail(entity, time_string)
                            
                            # If it is still not being recognized
                            if entity_extracted is False:
                                self.graceful_fail(message.chat.id)
                                
                        

                        self.form_action(message.chat.id)
                            

        while True:
            try:
                self.bot.polling()
            except Exception:
                time.sleep(15) 
    
    def show_entity_menu(self, chat_id):
        '''
        This generates an entity menu for users to edit
        '''

        markup = self.entity_menu_markup()
        message_id = self.bricks[chat_id]['menu_msg']['id']
        text = self.bricks[chat_id]['menu_msg']['text']

        self.bot.edit_message_text(chat_id=chat_id, message_id=message_id,text=text,
                         reply_markup=markup, parse_mode="Markdown")

    def graceful_fail(self, chat_id):
        '''
        This function handles unrecognised input
        Parameters:
        chat_id (int): The chat ID of telegram channel
        Return:
        None
        '''
        logging.info("Reach graceful fail")
        # Access the event currently being processed
        # in this chat
        event = self.bricks[chat_id]['event']

        # Get the entity we are requesting
        entity = event.get_req_entity()
        logging.info("Entity is {}".format(entity))
        # Generate a query
        
        text = ""
        
    
        if entity == 'date':
            text = "Couldn't understand the date \n" 
            text += "Please enter in format *dd/mm/yy*"
        
        elif entity == 'time':
            text = "Couldn't understand the time \n"
            text += "Please enter in 24 hour format like *HH:MM*"
        
        self.bot.send_message(chat_id, text, parse_mode="Markdown")

    def process_feedback(self, chat_id, feedback):
        '''
        This function processed feedback received from user
        Parameters:
        chat_id (int): Chat ID of telegram group
        Return:
        None
        '''
        
        if feedback is False:
            # Show the next event
            self.send_tracked_message(chat_id)
        
        else:
            logging.info("Positive feedback")

            # Get the current event and create an event object 

            item = self.bricks[chat_id]['cur_item']
            event = Event(item['event_type'])

            # Assign the event to the brick

            self.bricks[chat_id]['event'] = event

            event = self.bricks[chat_id]['event']

            # Automaticall detect some data
            message = self.bricks[chat_id]['cur_item']['text']
            
            # Extract date using sPacy NER
            doc = self.nlp(message)
            ents = [token.text for token in doc.ents  if token.label_ == 'DATE' ]
        
            if len(ents)>0:
                _date = ents[0]
                date_object = self.extract_date(_date)
                if date_object is not None:
                    date_string = self.get_date_string(date_object)
                    event.add_event_detail('date', date_string)
                    logging.info("Auto detect date")
            
                # Extract time using regex
                time_string = self.extract_time(message)

                if time_string is not None:
                    event.add_event_detail('time', time_string)
                    logging.info("Auto detect time")



            # Now start the process of collecting information
            self.form_action(chat_id)
        
    def form_action(self, chat_id):
        '''
        This function collects information of the event
        Parameters:
        chat_id : Chat ID of the telegram group
        Return:
        None
        '''
        logging.info("Form action invoked")
        # The event object can be easily accessed 
        # from the brick allocated to the chat



        event = self.bricks[chat_id]['event']


        if event.is_details_complete():

            # Get event details 
            event_details = event.get_event_details()
            
            # It is necessary to ask user
            # for correctness of data
            if event.are_details_valid():

                
                try:
                    connection = self.get_connection()
                    cursor = connection.cursor()

                    # Storing the event to database
                    insert_query = """INSERT INTO events (chat_id, type, description, date, time) VALUES (%s, %s, %s, %s, %s);"""

                    # Encrypt details
                    logging.info("Encrypting event details")
                    
                    event_details['description'] = self.goblin.encrypt(event_details['description'])
                    event_details['event_type'] = self.goblin.encrypt(event_details['event_type'])

                    record_to_insert = tuple([event_details[key] for key in event_details])
                    record_to_insert = (chat_id, )+record_to_insert

                    logging.info("Inserting into database")
                    cursor.execute(insert_query, record_to_insert)

                    # Commit
                    connection.commit()

                    cursor.close()
                    connection.close()
                    

                except (Exception,psycopg2.Error) as error:
                    logging.info(error)


                text = "Reminder set sucessfully"
                self.bot.send_message(chat_id, text, parse_mode='Markdown')

                # Clear the menu dictionary
                self.bricks[chat_id]['menu_msg'].clear()

                # Finally move onto next element
                self.send_tracked_message(chat_id)

            else:
                # Ask user if he want to make any changes to the data
                text = 'The details are \n'
                for event_key in event_details:
                    text+='\n '+'*'+event_key+'*'+' : '+event_details[event_key]
                
                markup = self.correctness_markup()

                sent_message = self.bot.send_message(chat_id, text, reply_markup=markup, 
                        parse_mode="Markdown")
                
                if self.bricks[chat_id]['menu_msg'] is None:
                    self.bricks[chat_id]['menu_msg'] = {}
                
                # Note menu message details
                self.bricks[chat_id]['menu_msg']['id']  = sent_message.message_id
                self.bricks[chat_id]['menu_msg']['text'] = text

        else:


            # Else query additional details

            query = ""

            # but first check if prev request was completed
            # to aid graceful failure

            condition = event.is_prev_req_complete()
            
            if condition:

                # Get the detail left to be filled
                entity = event.get_missing_detail()

                # Formulate a query
                query = "Please enter event "+entity
                query += "\n reply directly to this message"
            else:
                # Entity is same from the previous
                # incomplete query

                entity = event.get_req_entity() 

                # Request detail again
                query = "Please event "+entity+" again one more time"
                query += "\n reply directly to this message"

            
            # This bot is restricted to sending queries
            # The replies will be processed in the message handler

            sent_message = self.bot.send_message(chat_id, query)

            # Update the req_id
            self.bricks[chat_id]['req_id'] = sent_message.message_id
    
    def send_tracked_message(self, chat_id):
        '''
        This functions sends messages being tracked
        Parameters:
        chat_id (int): The chat ID of telegram group
        Return:
        None
        '''

        logging.info("Sending a tracked message")
        # A text will be generated in this function
        # and this text will be sent as message
        # Get the tracker item

        text = ""

        try:
        
            item = next(self.bricks[chat_id]['gen_event'])        
                
            # Store current item from tracker 
            # This item will be used for further processing
            self.bricks[chat_id]['cur_item'] = item
            
            # Extrapolate details from item 
            # and send message
            text = item['event_type']+" detected \n"
            text += '_'+item['text']+'_'+' \n'
            text += 'Set a reminder for this?'

            sent_message = self.bot.send_message(chat_id, text, 
                reply_markup=self.markup, 
                parse_mode="Markdown")

        except StopIteration:
            # All tracked messages have been sent already

            logging.info("Iteration Stopped")
            
            text = "You are all caught up :)"

            sent_message = self.bot.send_message(chat_id, text, 
                parse_mode="Markdown")

            # Empty the tracker
            
            connection = self.get_connection()
            
            cursor = connection.cursor()
            
            delete_query = "DELETE FROM tracker where chat_id= %s;"
            
            cursor.execute(delete_query, (str(chat_id),))
           
            # Commit changes
            connection.commit()
        
            cursor.close()
            connection.close()

            
            logging.info("Cleared tracked messages")

            # Destroy the brick
            # unless you want someone to DOS
            # the shit out of the bot

            del self.bricks[chat_id]

            # well you can only dereference
            # and garbage collector will deal with it

        except Exception as error:
            logging.info(error)
   
    def store_message(self, message):
        '''
        This function stores a message in database
        Parameters:
        chat_id (int): Chat ID of the telegram group
        message (string): The message to be stored
        Return:
        None
        '''
        event_type = self.extract_event(message.text)

        if event_type is None:
            event_type = 'Some event'
        
        
        try: 
            connection = self.get_connection()
            cursor = connection.cursor()   

            # Insert the message into postgres
            insert_query = """INSERT INTO tracker (chat_id, message, event_type) VALUES (%s,%s, %s);"""
            # Encrypt here

            message_text = self.goblin.encrypt(message.text)
            if message_text is None:
                raise Exception("Failed to encrypt")
            logging.info("Message encrypted")
            record_to_insert = (message.chat.id, message_text, event_type)
            logging.info("Inserting event into database")
            cursor.execute(insert_query, record_to_insert)

            #Commit the insert
            connection.commit()

            #Close the cursor
            cursor.close()
            connection.close()
            logging.info("Connection being closed")

        except (Exception, psycopg2.Error) as error:
                logging.info(error)

        finally:
            if cursor:
                cursor.close()

            if connection:
                connection.close()
                        
    def generate_brick(self):
        '''
        This function generates a dictionary 
        '''

        brick = {}

        for key in ['event', 'req_id', 'gen_event', 'cur_item', 'menu_msg']:
            brick[key] = None
        
        return brick
    
    def get_event_generator(self, tracker):
        '''
        This function is a generator which yields tracked messages
        Parameters:
        tracker (list) : List of messages being tracke
        Return:
        None
        '''

        for item in tracker:
            yield(item)
                  
    def gen_markup(self):
        '''
        This function generates markup for inline keyboard
        Parameters:
        None
        Return:
        None
        '''
        logging.info("Markup being generated")
        markup = types.InlineKeyboardMarkup()
        markup.row_width = 2
        markup.add(types.InlineKeyboardButton("Yes", callback_data="cb_yes"),
                    types.InlineKeyboardButton("No", callback_data="cb_no"))
        
        return markup

    def correctness_markup(self):
        '''
        This function generates markup for inline keyboard
        Parameters:
        None
        Return:
        None
        '''
        
        logging.info("Markup being generated")
        
        markup = types.InlineKeyboardMarkup()
        markup.row_width = 2
        markup.add(types.InlineKeyboardButton("Set Reminder", callback_data="store"),
                    types.InlineKeyboardButton("Dont set reminder", callback_data="storent"),
                    types.InlineKeyboardButton("Edit some details", callback_data="edit"))
        
        return markup

    def entity_menu_markup(self):
        '''
        This function generates markup for inline keyboard
        Parameters:
        None
        Return:
        None
        '''
        
        logging.info("Markup being generated")
        
        markup = types.InlineKeyboardMarkup()
        markup.row_width = 2
        markup.add(types.InlineKeyboardButton("Edit date", callback_data="date"),
                    types.InlineKeyboardButton("Edit Time", callback_data="time"),
                    types.InlineKeyboardButton("Go back", callback_data="back"))
        
        return markup       

    def get_connection(self):
        '''
        This function return a connection to the database
        Parameters:
        None
        Return:
        None
        '''

        try:
            connection = psycopg2.connect(
               os.environ['DATABASE_URL'],
                sslmode = 'require'
            )

            return connection
        
        except (Exception, psycopg2.Error) as error:
            logging.info(error)
            return None
    
    def retrieve_tracker(self, chat_id):
        '''
        This function retrieves the tracked messages from database
        Parameters:
        chat_id (int) : Chat ID of telegram group
        Return:
        list : A list of tracked messages from database
        '''

        tracker = []

        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            select_query = "SELECT * FROM tracker WHERE chat_id="+str(chat_id)
            logging.info("Querying from database")
            cursor.execute(select_query)

            records = cursor.fetchall()

            cursor.close()
            connection.close()

            
            for row in records:
                item = self.get_tracker_item(row)
                #Decrypt message here
                item['text'] = self.goblin.decrypt(item['text'])
                tracker.append(item)
    
        except Exception as error:
            logging.info(error)
        
        finally:
            return tracker

    def get_tracker_item(self, row):
        '''
        This function generates the item stored in tracker
        Parameters:
        message (string): The message to be processed
        Return:
        dictionary: The item to be added to tracker
        '''

        item = {
            'id':row[0],
            'chat_id':row[1],
            'text':row[2],
            'event_type':row[3]
        }

        return item

    def extract_event(self, message_text):
        '''
        This function extracts events from the message
        NER not able to parse the message

        Parameters:
        message_text (string) : The text message that was given

        Return:
        None
        '''

        events = ['Meeting', 'Party', 'DA', 'Exam', 'Assignement', 'Project','Post']

        for event in events:
            if re.search(event, message_text, re.IGNORECASE):
                return event
        return None
        
    def is_event_notification(self, message_text):
        '''
        This function checks if message is important or not
        Parameters:
        message_text (string) : The message from user
        Return:
        bool : True if message is important else False
        '''

        try:

            logging.info('Event notification being checked')
            
            sent2vec = self.get_embedding(message_text)
            ort_inputs = {self.model.get_inputs()[0].name: sent2vec}
            ort_outs = self.model.run(None, ort_inputs)
            logging.info("Confidence: {}".format(ort_outs[0][0]))
            if ort_outs[0][0]>0.8:
                return True
            else:
                return False
                
        except Exception as error:
            logging.info(repr(error))
            return False
    
    def get_embedding(self, text):
        """
        Returns the custom embeddings for message classification
        Parameters:
        text (string): User's message
        Return:
        np.ndarray: 192 dimensional embeddings
        """

        doc = self.nlp(text)
        word2vec = np.sum(np.array([token.vector for token in doc]),axis=0)/len(doc)
        sent2vec = doc.vector
        return np.concatenate([word2vec, sent2vec])

    def extract_date(self, text):
        '''
        This function extracts date from the text
        Parameters:
        text (string): User's reply which contains date
        '''
        logging.info("Using date extractor")
        # Assuming that there is only
        # one date

        # First with datefinder
        
        match_generator = datefinder.find_dates(text)
        date_object = None
        
        try:
        
            date_object = next(match_generator)
           
        
        except StopIteration:
        
          
            # Incase ppl forget how tomorrow is spelled
            var_of_tmrw = ['tommorrow','tmrw','tomorrow','tomorow']
            for word in text.split(' '):
                if word.lower() in var_of_tmrw:
                    text = "tomorrow"

            # Try tsresolve
            date_string = point_of_time(text)[0]

            if date_string is not None:
                
                date_string = date_string.split('T')[0]

                format = '%Y-%m-%d'

                date_object = datetime.strptime(date_string, format)

        finally:
        
            if date_object is None:
                logging.info("Date couldn't be extracted")
            return date_object

    def strip_time(self, text):
        '''
        This fuction trips time using time module
        Parameters:
        text (string): Where time is confirmed to be present 
        '''

        try:
            
            _day_pattern = r'am|pm|AM|PM'
            match = re.search(_day_pattern, text)
            
            if match is not None:
                _day = match.group()
            else:
                raise Exception("Day of time could not be detected")
            
            _time = re.sub(_day_pattern, "", text).strip()

            if _time.find(':') == -1:
                t = time.strptime(_time,'%H')
            else:
                t = time.strptime(_time, '%H:%M')

            hour = str(t.tm_hour) if _day.lower() == 'am' else str(t.tm_hour+12) if t.tm_hour != 12 else "12"
            mins = str(t.tm_min)

            if len(mins) == 1:
                mins = "0"+mins
            if len(hour) == 1:
                hour = "0"+hour
            time_string = hour+":"+mins

            return time_string
        
        except Exception as error:
            logging.info(repr(error))

    

    def extract_time(self, text):
        '''
        This function extracts time from the text
        Parameters:
        text (string): User's reply which contains time
        '''

        try:    
            
                pattern1 = r'\s(\d{1,2}\:\d{1,2}\s?(?:AM|PM|am|pm))'
                pattern2 = r'\s(\d{1,2}\s?(?:AM|PM|am|pm))'

                text = text.rjust(len(text)+1)

                match1 = re.findall(pattern1, text)
                match2 = re.findall(pattern2, text)

                match = match1+match2

                if len(match) != 0:
                                       
                    time_string = self.strip_time(match[0].strip())
                    
                    return time_string
                
                else:
                    
                    # Parse date and time from string
                    date_string = point_of_time(text)[0]
                    
                    # If parsed successfully
                    if date_string is not None:
                        
                        # Get the time part from the string
                        date_string = date_string.split('T')[1]
                        
                        # Form a datetime object 
                        date_format =  '%H:%M:%S'
                        date_object = datetime.strptime(date_string, date_format)
                        
                        # If able to form the datetime object
                        # which should be always possible
                        if date_object is not None:
                            # Get hour and minute
                            # clock = 'am'
                            hour = str(date_object.hour)
                            mins = str(date_object.minute)
                

                            if len(mins) == 1:
                                mins = "0"+mins

                            if len(hour) == 1:
                                hour = "0"+hour
                            
                            # Return time string
                            time_string = hour+":"+mins
                            

                            return time_string  


        except Exception as error:
            logging.info(error)        
                
        return None


    def send_stored_messages(self, chat_id):
        '''
        This function fetches stored messages from 
        database and prints them
        '''
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            
            cur_date = self.get_date_string(datetime.now())
            next_date = self.get_date_string(datetime.now() + timedelta(days=4)) 

            select_query = "SELECT * FROM events WHERE chat_id="+str(chat_id)
            select_query += " and date between '"+cur_date+"' and '"+next_date+"' ;"
            
            logging.info("Querying from database")
            cursor.execute(select_query)

            records = cursor.fetchall()
            
            if len(records) == 0:
                self.bot.send_message(chat_id, "There were no reminders")
            
            else:
            
                for row in records:
                    # Decrypt messages here
                    event_type = self.goblin.decrypt(row[2])
                    event_desc = self.goblin.decrypt(row[3])
                    date_string = self.get_date_string(row[4])

                    text = event_type + " on *"+date_string+"* at *"+row[5]+"*\n"+"_"+event_desc+"_"
                    self.bot.send_message(chat_id,text,parse_mode="Markdown")

                self.bot.send_message(chat_id, "You are all caught up :)")
            
            cursor.close()
            connection.close()
            logging.info("Connection closed")

        except Exception as error:
            logging.info(error)

    def get_date_string(self, date_object):
        '''
        This function returns a string representation
        of a datetime object
        Parameters:
        date_object (datetime): The date extracted from message
        Return:
        string : Date in form "dd/mm/yyyy"
        '''

        date_string = str(date_object.year) + '-'
        date_string += str(date_object.month) + '-'
        date_string += str(date_object.day)

        return date_string

    def send_help_message(self, chat_id):
        '''
        This function sends the help message
        Parameters:
        chat_id (int) : The Telegram group ID
        Return:
        None
        '''
        help_file = open("./core/help_message.txt", "r", encoding='utf-8')
        help_message = help_file.read()

        self.bot.send_message(chat_id, help_message, parse_mode="HTML")
