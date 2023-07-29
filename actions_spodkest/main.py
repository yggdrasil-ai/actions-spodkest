from flask import Flask, request
import functions_framework
import logging
logging.basicConfig(level=logging.INFO)
import os
import gcsfs
import json
import requests
from pdfminer.high_level import extract_text
import openai
from nltk.tokenize import word_tokenize
import nltk
nltk.download('punkt')

PROJECT_ID = os.environ.get('PROJECT_ID')
SPODKEST_FOLDER = os.environ.get('SPODKEST_FOLDER')
ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_KEY')
VOICE_INTRODUCTION = os.environ.get('VOICE_INTRODUCTION')
VOICE_SECTION = os.environ.get('VOICE_SECTION')
VOICE_CLOSURE = os.environ.get('VOICE_CLOSURE')

APP = Flask("internal")
fs = gcsfs.GCSFileSystem()
openai.api_key = os.environ.get('OPENAI_KEY')

def generate_answer(prompt, message_list, model, attempt=0):
        """
        This function will continue the conversation.
        Parameters:
            conversation_info: The information of the conversation
            message: The message to append
        """
        logging.info("Generating answer")
        messages = [{"role": "system", "content": prompt}]
        messages.extend([{"role": "user", "content": message} for message in message_list])
        print(messages)
        full_prompt = {
            "model": model,
            "messages": messages
        }

        logging.info("Calling OpenAI: {}".format(full_prompt))

        try:
          response = openai.ChatCompletion.create(**full_prompt)["choices"][0]["message"]["content"]
        except Exception as e:
          if attempt < 3:
            response = generate_answer(prompt, message_list, model, attempt+1)
          else:
            raise e
        return response

def read_file(file):
    with fs.open(file, 'r') as f:
        content = f.read()
    return content

def read_bytes(file):
    with fs.open(file, 'rb') as f:
        content = f.read()
    return content

def write_to_file(file, content):
    with fs.open(file, "w+") as file_:
        file_.seek(0) # set the pointer to beginning of file (truncate any existing data
        file_.write(content)

def download_file(url, destiny_file):
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with fs.open(destiny_file, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return destiny_file
    

def summarizer(text, max_tokens=2000):
    """Summarize a given piece of text using GPT-3"""
    # Tokenize text and split into chunks of 2000 tokens
    SUMMARIZER_PROMPT = """You are a text analyst. You will receive a fragment of a text and you should summarize it, and select its more original and remarkable statements and present them in a particular format. Example:
    ```
    user: very advanced school, by amusing the poor.
    But this is not a solution: it is an aggravation of the difficulty. The proper
    aim is to try and reconstruct society on such a basis that poverty will be
    impossible. And the altruistic virtues have really prevented the carrying out of
    this aim. Just as the worst slave-owners were those who were kind to their
    slaves, and so prevented the horror of the system being realised by those who
    suffered from it, and understood by those who contemplated it, so, in the
    present state of things in England, the people who do most harm are the people
    who try to do most good; and at last we have had the spectacle of men who
    have really studied the problem and know the life-educated men who live in
    the East End—coming forward and imploring the community to restrain its
    altruistic impulses of charity, benevolence, and the
    assistant: #summary#
    The argument criticizes the prevailing approach to addressing poverty, which it considers not only ineffective but also harmful. Instead of focusing on "amusing the poor" or temporarily relieving their suffering, it suggests that society should be restructured to prevent poverty from existing in the first place. The text interestingly equates altruistic virtues, like charity and benevolence, to a form of slavery, as these actions, though seemingly kind, prevent the true severity of poverty from being fully understood and addressed.

    #original statements#
    - comparison between well-intentioned but potentially harmful philanthropists and kind slave-owners.
    - plea from those who have studied the problem and live in the impacted areas, asking the community to reconsider its charitable actions.
    ```
    It's very important to use #summary# and #original statements# flags as used in the example, and to use a list to separate original statements.
    """

    SUMMARIZATION_MAPREDUCE_PROMPT = """You coordinate a team of text analysts.
    Each text analyst have read a section from a text and has extracted summary and original statements. 
    Your job is putting all analysts' work together. Extract the combined summary and original statements. 
    They don't have to convey the same information, summary has to reflect the general arguments of the text, while original statements must be a selection of concrete, original points.
    Summary and original statements must always provide different information, summary shouldn't be deductible from original statements.
    Your answer should have a #summary# and an #original statements# section"""
        

    tokens = word_tokenize(text)
    chunks = [tokens[i:i + 2000] for i in range(0, len(tokens), max_tokens)]

    # Summarize each chunk
    summaries = [generate_answer(SUMMARIZER_PROMPT, [' '.join(chunk)], "gpt-3.5-turbo") for chunk in chunks]

    def reduce_summaries(summaries):
        # Pack summaries into groups with sum of tokens <= 3000
        summary_groups = []
        current_group = []
        current_group_tokens = 0
        for summary in summaries:
            summary_tokens = len(word_tokenize(summary))
            if current_group_tokens + summary_tokens > 2000:
                # Start a new group
                summary_groups.append(current_group)
                current_group = [summary]
                current_group_tokens = summary_tokens
            else:
                # Add to the current group
                current_group.append(summary)
                current_group_tokens += summary_tokens
        # Add the last group if it isn't empty
        if current_group:
            summary_groups.append(current_group)
        mapreduced = [generate_answer(SUMMARIZATION_MAPREDUCE_PROMPT, group, "gpt-3.5-turbo") for group in summary_groups]
        return mapreduced
    
    while len(summaries) > 1:
        summaries = reduce_summaries(summaries)
    
    return summaries[0]

def parse_sections(text):
    # Split the text into sections
    sections = text.split("#section")[1:]  # The first item is empty, so we skip it

    # Remove leading and trailing whitespace from each section
    sections = [section.strip() for section in sections]

    return sections

def generate_introduction(podcast_plan, requirements):
    GENERATE_INTRODUCTION_PROMPT = """You are a podcast speaker. You should write the introduction of a podcast which skeleton will be provided by the user.
    Keep it really short and interesting. You don't have to include all data, just to present the podcast, your colleagues will do the different sections after you.
    Keep it short. You should comply with this requirements: {podcast_requirements}"""
    return generate_answer(GENERATE_INTRODUCTION_PROMPT.format(podcast_requirements=requirements), [podcast_plan], "gpt-3.5-turbo")

def generate_closure(podcast_plan, requirements):
    GENERATE_CLOSURE_PROMPT = """You are a podcast speaker. You should write the closure of a podcast which skeleton will be provided by the user.
    Keep it short and engaging. You don't have to talk about all topics, as your colleagues have already tackled them.
    You should comply with this requirements: {podcast_requirements}"""
    return generate_answer(GENERATE_CLOSURE_PROMPT.format(podcast_requirements=requirements), [podcast_plan], "gpt-3.5-turbo")




def process_input_files(workspace, input_files=None):
    if not input_files:
        # Get list of files in {workspace}/input_files
        input_files = fs.glob(f'{workspace}/input_files/')
    summaries = []
    for file in input_files:
        # Extract text
        print('Processing:', file)
        extracted_text = extract_text(read_bytes(file))
        summarized_text = summarizer(extracted_text)
        filename = file.split('/')[-1]
        summaries += [summarized_text]
        write_to_file(f"{workspace}/input_summaries/{filename}", summarized_text)
    return summaries

def generate_skeleton(workspace, requirements=None, summaries=None):
    GENERATE_STRUCTURE_PROMPT = """
    You are a podcast planner. You must create the skeleton of a podcast based on different summaries of some arguments, each with some original statements that must be stated in different moments of the podcast.
    You should divide it in sections, with the following structure:
    ```
    #section <number>#
    - Title: section title
    - Ideas: ideas section must talk about
    - Original statements: original statements that should appear in this section, separated by commands
    ```
    The podcast must comply with the following requirements: {podcast_requirements}
    """

    if not requirements:
        requirements = read_file(f'{workspace}/requirements.txt')
    if not summaries:
        summary_files = fs.glob(f'{workspace}/input_summaries/')
        summaries = [read_file(file) for file in summary_files]
    podcast_plan = generate_answer(GENERATE_STRUCTURE_PROMPT.format(podcast_requirements=requirements), message_list=summaries, model="gpt-3.5-turbo")
    write_to_file(f'{workspace}/podcast_plan.txt', podcast_plan)
    sections = parse_sections(podcast_plan)
    introduction = generate_introduction(podcast_plan, requirements)
    write_to_file(f'{workspace}/introduction.txt', introduction)
    closure = generate_closure(podcast_plan, requirements)
    write_to_file(f'{workspace}/closure.txt', closure)
    return introduction, sections, closure

def generate_sections(workspace, sections = None, requirements = None):
    GENERATE_SECTION_PROMPT = """You are a speaker. You should write a section talking about some ideas and including some statements.
    You should comply with this requirements: {podcast_requirements}"""
    
    if not sections:
        sections = parse_sections(read_file(f'{workspace}/podcast_plan.txt'))
    if not requirements:
        requirements = read_file(f"{workspace}/requirements.txt")

    full_sections = []
    i = 0
    for section in sections:
        i+=1
        generated_section = generate_answer(GENERATE_SECTION_PROMPT.format(podcast_requirements=requirements), [section], "gpt-3.5-turbo")
        write_to_file(f'{workspace}/sections/section{i}.txt', generated_section)
        full_sections += [generated_section]
    return full_sections

def generate_podcast(workspace, introduction, sections, closure):
    if not introduction:
        introduction = read_file(f'{workspace}/introduction.txt')
    if not sections:
        section_files = fs.glob(f'{workspace}/sections/')
        sections = [read_file(file) for file in section_files]
    if not closure:
        closure = read_file(f'{workspace}/closure.txt')

    def generate_audio(voice, text, output_file):
        CHUNK_SIZE = 1024
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"

        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": ELEVENLABS_API_KEY
        }

        data = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.5
            }
        }
        
        response = requests.post(url, json=data, headers=headers)
        with fs.open(output_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
        return output_file  
    
    def combine_audios(audio_files, destiny_name):
        from pydub import AudioSegment
        downloaded_files = []
        for file in audio_files:
            filename = file.split('/')[-1]
            with fs.open(file, 'rb') as origin_file:
                with open(filename, 'wb') as destiny_file:
                    destiny_file.write(origin_file.read())
            downloaded_files += [filename]
        # Load your 'sintonia' mp3
        sintonia = AudioSegment.from_mp3("sintonia.mp3")

        # Initialize an empty audio segment
        combined = AudioSegment.empty()

        # Loop over your mp3 files
        for mp3_file in downloaded_files:
            # Load the current mp3 file
            sound = AudioSegment.from_mp3(mp3_file)

            # Append the current sound and the 'sintonia' to the combined audio
            combined += sound
            combined += sintonia

        # The final 'sintonia' is not needed, so we remove it
        combined = combined[:-len(sintonia)]

        # Export the combined audio
        combined.export("combined.mp3", format='mp3')

        with fs.open(destiny_name, 'wb') as destiny_file:
            with open("combined.mp3") as origin_file:
                destiny_file.write(origin_file.read())
        return destiny_name


    
    introduction_audio = generate_audio(VOICE_INTRODUCTION, introduction, f'{workspace}/introduction.mp3')
    section_audios = []
    i = 0
    for section in sections:
        i += 1
        section_audio = generate_audio(VOICE_SECTION, section, f'{workspace}/mp3_sections/section{i}.mp3')
        section_audios += [section_audio]
    closure_audio = generate_audio(VOICE_CLOSURE, closure, f'{workspace}/closure.mp3')
    audios = [introduction_audio]
    audios += section_audios
    audios += [closure_audio]
    podcast = combine_audios(audios, f'{workspace}/podcast.mp3')
    return podcast



@APP.route('/', methods=['GET', 'POST'])
def unknown_operation():
    response = APP.response_class(
        response="Incomplete path, please select an operation",
        status=400,
        mimetype='text/plain')
    return response

@APP.route('/create', methods=['POST', ])
def create_spodkest():
    logging.info("Received request: {}".format(request))
    request_json = json.loads(request.data)
    author = request_json["author"]
    if request_json["author"] == "#spokeAgent#":
        author = request_json["conversationId"].split(".")[0]
    user = request_json["user"] if request_json["user"]!="undefined" else author
    name = request_json["name"]
    requirements = request_json["requirements"]
    files = request_json["inputFiles"].split(',')

    # Ensure workspace setup
    assigned_folder = f"{SPODKEST_FOLDER}/{name}"
    if requirements == "undefined":
        requirements = read_file(f"{assigned_folder}/requirements.txt")
    else:
        write_to_file(f"{assigned_folder}/requirements.txt", requirements)
    owned_files = []
    if len(files) > 0 and files[0]!="undefined":
        for file in [x.strip() for x in files]:
            filename = file.split('/')[-1]
            saved_file = download_file(file, f'{assigned_folder}/input_files/{filename}')
            owned_files += [saved_file]
    else:
        logging.error("No input files")
        response = APP.response_class(
            response=json.dumps({"payload": {
                "error": "No input files"
            },
            "responseMessage": "ERROR: No input files"}),
            status=200,
            mimetype='text/plain'
        )
        return response
    

    # Process input files
    summaries = process_input_files(assigned_folder, owned_files)

    # Generate podcast skeleton
    introduction, sections, closure = generate_skeleton(assigned_folder, requirements, summaries)

    # Generate sections
    full_sections = generate_sections(assigned_folder, sections, requirements)

    # Generate podcast
    podcast = generate_podcast(assigned_folder, introduction, full_sections, closure)

    response = APP.response_class(
        response=json.dumps({"payload":{
            "url": podcast
            },
            "responseMessage": f"Podcast created and saved in {podcast}"}),
        status=200,
        mimetype='text/plain')
    return response
    
    
@functions_framework.http
def actions_spodkest(request):
    internal_ctx = APP.test_request_context(path=request.full_path,
                                            method=request.method)
    internal_ctx.request.data = request.data
    internal_ctx.request.headers = request.headers
    internal_ctx.request.args = request.args
    
    APP.config['PRESERVE_CONTEXT_ON_EXCEPTION']=False
    
    return_value = APP.response_class(
        response="Invalid Request", 
        status=400,
        mimetype='text/plain')
    
    try:
        internal_ctx.push()
        return_value = APP.full_dispatch_request()
        logging.info("Request processed: {}".format(return_value))
        internal_ctx.pop()
    except Exception as e:
        logging.error(e)
    return return_value