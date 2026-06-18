import random
import re

class CompanionGames:
    def __init__(self, tts, servos):
        self.tts = tts
        self.servos = servos
        self.active_game = None # None, 'riddle', 'word_chain', 'guess_number'
        
        # Game State Variables
        self.score = 0
        self.round_num = 0
        
        # Riddle State
        self.riddle_index = 0
        self.riddles = [
            {
                "question": "লাল গাভীটি ঘাস খায়, জল খেলেই মরে যায়? বলো তো কী?",
                "answers": ["আগুন", "fire", "aagun", "agun"],
                "hint": "এটি খুব গরম এবং এর সাহায্যে আমরা রান্না করি বা আলো জ্বালাই।",
                "success": "বাহ! একদম সঠিক উত্তর দিয়েছ। উত্তরটি হলো আগুন। [expression: happy]"
            },
            {
                "question": "তিন অক্ষরের নাম তার জলে বাস করে, প্রথম অক্ষর বাদ দিলে আকাশেতে ওড়ে? বলো তো কী?",
                "answers": ["মাছ", "fish", "maach", "mach"],
                "hint": "এটি জলে সাঁতার কাটে, আর চিল আকাশে ওড়ে (মাছ থেকে ম বাদ দিলে হয় চিল)।",
                "success": "চমৎকার বুদ্ধি তোমার! একদম ঠিক, উত্তরটি হলো মাছ। [expression: excited]"
            },
            {
                "question": "একটি ঘরের কোণে বসে থাকে, কিন্তু সারা পৃথিবী ঘুরে বেড়ায়? বলো তো কী?",
                "answers": ["ডাকটিকিট", "টিকিট", "স্ট্যাম্প", "stamp", "ticket", "postage"],
                "hint": "এটি চিঠির খামের ওপর লাগানো থাকে।",
                "success": "অসাধারণ! একদম ঠিক ধরেছো। উত্তরটি হলো ডাকটিকিট। [expression: happy]"
            },
            {
                "question": "কাঁচাতে সবুজ থাকে, পাকলে হয় লাল, এর ঝাল খেয়ে সবার চোখ ফাটে জলাল? বলো তো কী?",
                "answers": ["লঙ্কা", "মরিচ", "কাঁচালঙ্কা", "কাঁচা লঙ্কা", "chili", "chilli", "pepper"],
                "hint": "এটি রান্নায় ঝাল বাড়ানোর জন্য ব্যবহার করা হয়।",
                "success": "একদম ঠিক! উত্তরটি হলো লঙ্কা বা মরিচ। চমৎকার বুদ্ধি তোমার! [expression: happy]"
            }
        ]
        
        # Word Chain State
        self.last_char = ""
        # Vocabulary of Bengali words mapping starting consonants to standard response words
        self.word_chain_vocab = {
            "ক": ("কলকাতা", "ত"),
            "খ": ("খেলনা", "ন"),
            "গ": ("গান", "ন"),
            "ঘ": ("ঘড়ি", "ড়"),
            "চ": ("চাঁদ", "দ"),
            "ছ": ("ছবি", "ভ"),
            "জ": ("জীবন", "ন"),
            "ঝ": ("ঝড়", "ড়"),
            "ট": ("টমেটো", "ট"),
            "ঠ": ("ঠাণ্ডা", "ড"),
            "ড": ("ডাব", "ব"),
            "ঢ": ("ঢোল", "ল"),
            "ত": ("তরমুজ", "জ"),
            "থ": ("থালা", "ল"),
            "দ": ("দেশ", "শ"),
            "ধ": ("ধান", "ন"),
            "ন": ("নদী", "দ"),
            "প": ("পাখি", "খ"),
            "ফ": ("ফুল", "ল"),
            "ব": ("বই", "ই"),
            "ভ": ("ভারত", "ত"),
            "ম": ("মানুষ", "ষ"),
            "য": ("যত্রতত্র", "ত"),
            "র": ("রংধনু", "ন"),
            "ল": ("লাল", "ল"),
            "শ": ("শাপলা", "ল"),
            "ষ": ("ষাঁড়", "ড়"),
            "স": ("সূর্য", "য"),
            "হ": ("হাত", "ত"),
            "ড়": ("ড়ুংরি", "র"),
            "য়": ("য়াক", "ক")
        }
        
        # Guess Number State
        self.secret_number = 0
        self.guess_count = 0

    def start_game(self, game_name, lang="bn-IN"):
        """Starts a game and speaks the initial welcome prompt."""
        self.active_game = game_name.lower()
        self.score = 0
        self.round_num = 1
        
        if self.active_game == "riddle":
            self.riddle_index = 0
            prompt = "চলুন, ধাঁধা খেলা যাক! আমি একটি ধাঁধা বলছি, আপনি উত্তর দেওয়ার চেষ্টা করুন। আমার প্রথম ধাঁধাটি হলো: " + self.riddles[self.riddle_index]["question"]
            self.servos.play_gesture("nod")
            self.tts.speak(prompt, lang)
            return prompt
            
        elif self.active_game == "word_chain":
            self.last_char = "ত"
            prompt = "চলুন, शब्द-শৃঙ্খল খেলা যাক! আমি একটি বাংলা শব্দ বলবো, আপনাকে তার শেষ ব্যঞ্জনবর্ণ দিয়ে একটি নতুন শব্দ বলতে হবে। আমার প্রথম শব্দ হলো: 'কলকাতা'। শেষ অক্ষর 'ত'। এবার আপনার পালা, ত দিয়ে একটি শব্দ বলুন!"
            self.servos.play_gesture("scanning")
            self.tts.speak(prompt, lang)
            return prompt
            
        elif self.active_game == "guess_number":
            self.secret_number = random.randint(1, 20)
            self.guess_count = 0
            prompt = "আসুন, সংখ্যা খোঁজার খেলা খেলি! আমি মনে মনে ১ থেকে ২০ এর মধ্যে একটি সংখ্যা ভেবেছি। বলো তো সংখ্যাটি কত হতে পারে?"
            self.servos.play_gesture("think")
            self.tts.speak(prompt, lang)
            return prompt
            
        return ""

    def stop_game(self, lang="bn-IN"):
        """Stops the active game cleanly."""
        if not self.active_game:
            return ""
            
        prompt = f"খুব সুন্দর খেলা হলো বন্ধু! আপনার মোট স্কোর হলো {self.score}। আবার পরে খেলবো! [expression: happy]"
        self.active_game = None
        self.tts.speak(prompt, lang)
        self.servos.play_gesture("nod")
        return prompt

    def get_last_bengali_consonant(self, word):
        """Extracts the last valid consonant (ignoring vowel signs and markers) from a Bengali word."""
        vowels_and_marks = set([
            'া', 'ি', 'ী', 'ু', 'ূ', 'ে', 'ৈ', 'ো', 'ৌ', '্', 'ঁ', 'ঃ', 'ং', 'ৎ',
            'অ', 'আ', 'ই', 'ঈ', 'উ', 'ঊ', 'ঋ', 'এ', 'ঐ', 'ও', 'ঔ'
        ])
        clean_word = word.strip()
        for char in reversed(clean_word):
            if char not in vowels_and_marks:
                return char
        return clean_word[-1] if clean_word else ''

    def get_first_bengali_char(self, word):
        """Extracts the first valid character/consonant of a Bengali word."""
        clean_word = word.strip()
        return clean_word[0] if clean_word else ''

    def handle_input(self, user_speech, lang="bn-IN"):
        """Processes user voice inputs for the active game state and returns the companion's response."""
        if not self.active_game:
            return ""
            
        speech_lower = user_speech.lower().strip()
        
        # Stop commands
        if any(p in speech_lower for p in ["খেলা বন্ধ", "বন্ধ করো", "খেলব না", "stop game", "exit game", "খেলবো না"]):
            return self.stop_game(lang)
            
        if self.active_game == "riddle":
            riddle = self.riddles[self.riddle_index]
            # Check if user wants a hint
            if any(h in speech_lower for h in ["হিন্ট", "ইঙ্গিত", "hint", "সাহায্য"]):
                prompt = f"ইঙ্গিতটি হলো: {riddle['hint']}. বলুন তো উত্তরটি কী?"
                self.tts.speak(prompt, lang)
                return prompt
                
            # Check answer
            is_correct = False
            for ans in riddle["answers"]:
                if ans in speech_lower:
                    is_correct = True
                    break
                    
            if is_correct:
                self.score += 10
                prompt = riddle["success"]
                self.riddle_index = (self.riddle_index + 1) % len(self.riddles)
                self.round_num += 1
                prompt += f" চলুন, পরের ধাঁধা ধরা যাক। ধাঁধা নম্বর {self.round_num}: " + self.riddles[self.riddle_index]["question"]
                self.servos.play_gesture("nod")
                self.tts.speak(prompt, lang)
                return prompt
            else:
                prompt = "ভুল উত্তর বন্ধু! আবার চেষ্টা করুন, অথবা ইঙ্গিত পেতে 'ইঙ্গিত দাও' বলুন।"
                self.servos.play_gesture("shake")
                self.tts.speak(prompt, lang)
                return prompt
                
        elif self.active_game == "word_chain":
            last_char_expected = self.last_char
            words = speech_lower.split()
            
            # Find a word starting with last_char_expected
            chosen_word = None
            for w in words:
                if self.get_first_bengali_char(w) == last_char_expected:
                    chosen_word = w
                    break
                    
            if not chosen_word:
                # Fallback to the first word if none matched the expected character
                chosen_word = words[0] if words else user_speech
                
            first_char = self.get_first_bengali_char(chosen_word)
            
            # Validate if it matches the expected starting character
            if first_char != last_char_expected:
                prompt = f"ভুল ব্যঞ্জনবর্ণ বন্ধু! আপনাকে '{last_char_expected}' দিয়ে শুরু হওয়া শব্দ বলতে হবে। আবার চেষ্টা করুন!"
                self.servos.play_gesture("shake")
                self.tts.speak(prompt, lang)
                return prompt
                
            # User gave a valid word! Let's find its last consonant
            user_last = self.get_last_bengali_consonant(chosen_word)
            if not user_last:
                prompt = "দয়া করে একটু স্পষ্ট করে বলুন।"
                self.tts.speak(prompt, lang)
                return prompt
                
            # Look up a word from our vocabulary starting with user_last
            lookup = self.word_chain_vocab.get(user_last)
            if lookup:
                robot_word, robot_next = lookup
                self.last_char = robot_next
                self.score += 5
                prompt = f"চমৎকার! আপনি বললেন '{chosen_word}'। আমার শব্দ হলো '{robot_word}'। শেষ অক্ষর '{robot_next}'। এবার '{robot_next}' দিয়ে বলুন!"
                self.servos.play_gesture("nod")
                self.tts.speak(prompt, lang)
                return prompt
            else:
                # Fallback if we don't have a word in database
                robot_word = "বিজ্ঞান" # ends in 'ন'
                self.last_char = "ন"
                self.score += 5
                prompt = f"দারুণ শব্দ! আমার শব্দ হলো 'বিজ্ঞান'। শেষ অক্ষর 'ন'। এবার 'ন' দিয়ে বলুন!"
                self.servos.play_gesture("nod")
                self.tts.speak(prompt, lang)
                return prompt
                
        elif self.active_game == "guess_number":
            self.guess_count += 1
            
            # Map spoken Bengali word numbers to integers
            bengali_word_to_num = {
                "এক": 1, "দুই": 2, "তিন": 3, "চার": 4, "পাঁচ": 5, "পাচ": 5,
                "ছয়": 6, "ছয়": 6, "সাত": 7, "আট": 8, "নয়": 9, "নয়": 9, "দশ": 10,
                "এগারো": 11, "বারো": 12, "তেরো": 13, "চৌদ্দ": 14, "পনেরো": 15,
                "ষোল": 16, "ষোলো": 16, "সতেরো": 17, "আঠারো": 18, "উনিশ": 19, "বিশ": 20, "কুড়ি": 20, "কুড়ি": 20
            }
            
            word_val = None
            for word, num in bengali_word_to_num.items():
                if word in speech_lower:
                    word_val = num
                    break
                    
            val = None
            if word_val is not None:
                val = word_val
            else:
                # Extract integer from user's speech
                nums = re.findall(r'\d+', speech_lower)
                # Check Bengali digits as well
                bn_digits = {'১':'1', '২':'2', '৩':'3', '৪':'4', '৫':'5', '৬':'6', '৭':'7', '৮':'8', '৯':'9', '০':'0'}
                bengali_nums = ""
                for char in speech_lower:
                    if char in bn_digits:
                        bengali_nums += bn_digits[char]
                
                if nums:
                    try:
                        val = int(nums[0])
                    except Exception:
                        pass
                elif bengali_nums:
                    try:
                        val = int(bengali_nums)
                    except Exception:
                        pass
                
            if val is None:
                prompt = "অনুগ্রহ করে ১ থেকে ২০ এর মধ্যে একটি সংখ্যা বলুন।"
                self.tts.speak(prompt, lang)
                return prompt
                
            if val == self.secret_number:
                self.score += max(5, 20 - self.guess_count * 2)
                prompt = f"একেবারে সঠিক উত্তর! আমি {self.secret_number} সংখ্যাটিই ভেবেছিলাম। তুমি {self.guess_count} বারে উত্তরটি খুঁজে পেয়েছো! [expression: happy]"
                self.tts.speak(prompt, lang)
                self.servos.play_gesture("nod")
                # Automatically reset with a new number
                self.secret_number = random.randint(1, 20)
                self.guess_count = 0
                prompt += " চলুন, আরেকটি খেলা যাক! আমি মনে মনে ১ থেকে ২০ এর মধ্যে আরেকটি নতুন সংখ্যা ভেবেছি। বলো তো সংখ্যাটি কত হতে পারে?"
                self.tts.speak("চলুন, আরেকটি খেলা যাক! আমি মনে মনে ১ থেকে ২০ এর মধ্যে আরেকটি নতুন সংখ্যা ভেবেছি। বলো তো সংখ্যাটি কত হতে পারে?", lang)
                return prompt
            elif val < self.secret_number:
                prompt = "সংখ্যাটি এর চেয়ে বড়! আবার বলো।"
                self.servos.play_gesture("think")
                self.tts.speak(prompt, lang)
                return prompt
            else:
                prompt = "সংখ্যাটি এর চেয়ে ছোট! আবার বলো।"
                self.servos.play_gesture("think")
                self.tts.speak(prompt, lang)
                return prompt
                
        return ""
