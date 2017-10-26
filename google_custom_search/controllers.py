# google_custom_search/controllers.py
# Brought to you by We Vote. Be good.
# -*- coding: UTF-8 -*-

# See also WeVoteServer/import_export_twitter/controllers.py for routines that manage incoming twitter data

from .models import GoogleSearchUserManager
from config.base import get_environment_variable
from googleapiclient.discovery import build
from import_export_wikipedia.controllers import reach_out_to_wikipedia_with_guess, \
    retrieve_candidate_images_from_wikipedia_page
from re import sub
from twitter.controllers import POSITIVE_KEYWORDS, NEGATIVE_KEYWORDS
from wevote_functions.functions import positive_value_exists

GOOGLE_SEARCH_ENGINE_ID = get_environment_variable("GOOGLE_SEARCH_ENGINE_ID")
GOOGLE_SEARCH_API_KEY = get_environment_variable("GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_API_NAME = get_environment_variable("GOOGLE_SEARCH_API_NAME")
GOOGLE_SEARCH_API_VERSION = get_environment_variable("GOOGLE_SEARCH_API_VERSION")
MAXIMUM_GOOGLE_SEARCH_USERS = 10


def delete_possible_google_search_users(candidate_campaign):
    status = ""
    google_search_user_manager = GoogleSearchUserManager()

    if not candidate_campaign:
        status += "DELETE_POSSIBLE_GOOGLE_SEARCH_USER-CANDIDATE_MISSING "
        results = {
            'success':                  False,
            'status':                   status,
        }
        return results

    results = google_search_user_manager.delete_google_search_users_possibilities(candidate_campaign.we_vote_id)
    status += results['status']

    results = {
        'success':                  True,
        'status':                   status,
    }

    return results


def retrieve_possible_google_search_users(candidate_campaign):
    status = ""
    possible_google_search_users_list = []
    google_search_user_manager = GoogleSearchUserManager()

    if not candidate_campaign:
        status = "RETRIEVE_POSSIBLE_TWITTER_HANDLES-CANDIDATE_MISSING "
        results = {
            'success':                  False,
            'status':                   status,
        }
        return results

    status += "RETRIEVE_POSSIBLE_TWITTER_HANDLES-REACHING_OUT_TO_TWITTER "

    search_term = candidate_campaign.candidate_name
    google_api = build(GOOGLE_SEARCH_API_NAME, GOOGLE_SEARCH_API_VERSION,
                       developerKey=GOOGLE_SEARCH_API_KEY)
    search_results = google_api.cse().list(q=search_term, cx=GOOGLE_SEARCH_ENGINE_ID, gl="countryUS",
                                           filter='1').execute()

    name_handling_regex = r"[^ \w'-]"
    candidate_name = {
        'title':       sub(name_handling_regex, "", candidate_campaign.extract_title()),
        'first_name':  sub(name_handling_regex, "", candidate_campaign.extract_first_name()),
        'middle_name': sub(name_handling_regex, "", candidate_campaign.extract_middle_name()),
        'last_name':   sub(name_handling_regex, "", candidate_campaign.extract_last_name()),
        'suffix':      sub(name_handling_regex, "", candidate_campaign.extract_suffix()),
        'nickname':    sub(name_handling_regex, "", candidate_campaign.extract_nickname()),
    }

    possible_google_search_users_list.extend(analyze_google_search_results(search_results, search_term, candidate_name,
                                                                           candidate_campaign))

    # Also include search results omitting any single-letter initials and periods in name.
    # Example: "A." is ignored while "A.J." becomes "AJ"
    modified_search_term = ""
    modified_search_term_base = ""
    if len(candidate_name['first_name']) > 1:
        modified_search_term += candidate_name['first_name'] + " "
    if len(candidate_name['middle_name']) > 1:
        modified_search_term_base += candidate_name['middle_name'] + " "
    if len(candidate_name['last_name']) > 1:
        modified_search_term_base += candidate_name['last_name']
    if len(candidate_name['suffix']):
        modified_search_term_base += " " + candidate_name['suffix']
    modified_search_term += modified_search_term_base
    if search_term != modified_search_term:
        modified_search_results = google_api.cse().list(q=modified_search_term, cx=GOOGLE_SEARCH_ENGINE_ID,
                                                        gl="countryUS", filter='1').execute()
        possible_google_search_users_list.extend(analyze_google_search_results(modified_search_results,
                                                                               modified_search_term, candidate_name,
                                                                               candidate_campaign))

    # If nickname exists, try searching with nickname instead of first name
    if len(candidate_name['nickname']):
        modified_search_term_2 = candidate_name['nickname'] + " " + modified_search_term_base
        modified_search_results_2 = google_api.cse().list(q=modified_search_term_2, cx=GOOGLE_SEARCH_ENGINE_ID,
                                                          gl="countryUS", filter='1').execute()
        possible_google_search_users_list.extend(analyze_google_search_results(modified_search_results_2,
                                                                               modified_search_term_2, candidate_name,
                                                                               candidate_campaign))

    wikipedia_page_results = retrieve_possible_wikipedia_page(search_term)
    if wikipedia_page_results['wikipedia_page_found']:
        update_results = update_google_search_with_wikipedia_results(wikipedia_page_results['wikipedia_page'],
                                                                     search_term, candidate_name, candidate_campaign,
                                                                     possible_google_search_users_list)
        if not update_results['wikipedia_user_exist_in_google_search']:
            possible_google_search_users_list.extend(update_results['possible_wikipedia_search_user'])

    success = bool(possible_google_search_users_list)
    possible_google_search_users_list.sort(key=lambda possible_candidate: possible_candidate['likelihood_score'],
                                           reverse=True)

    google_search_user_count = 0
    if success:
        status += "RETRIEVE_POSSIBLE_GOOGLE_SEARCH_USERS-RETRIEVED_FROM_GOOGLE"
        for possibility_result in possible_google_search_users_list:
            save_google_search_user_results = google_search_user_manager.\
                update_or_create_google_search_user_possibility(candidate_campaign.we_vote_id,
                                                                possibility_result['google_json'],
                                                                possibility_result['search_term'],
                                                                possibility_result['likelihood_score'])
            if save_google_search_user_results['success']:
                google_search_user_count += 1
                if google_search_user_count > MAXIMUM_GOOGLE_SEARCH_USERS:
                    break

    results = {
        'success':                  True,
        'status':                   status,
        'num_of_possibilities':     str(google_search_user_count),
    }

    return results


def analyze_google_search_results(search_results, search_term, candidate_name,
                                  candidate_campaign):
    total_search_results = 0
    possible_google_search_users_list = []
    election_name = candidate_campaign.election().election_name
    if positive_value_exists(search_results):
        total_search_results = (search_results.get('searchInformation').get('totalResults')
                                if 'searchInformation' in search_results.keys() and
                                search_results.get('searchInformation', {}).get('totalResults', 0) else 0)

    if positive_value_exists(total_search_results):
        all_search_items = search_results.get('items', [])
        for one_result in all_search_items:
            likelihood_score = 0
            google_json = parse_google_search_results(search_term, one_result)

            # If candidate_location is not same as election location then skip this search entry
            if google_json['item_person_location'] and \
                    google_json['item_person_location'].lower() not in election_name.lower():
                continue

            # Check if name (or parts of name) are in title, snippet and description
            name_found_in_title = False
            name_found_in_description = False
            for name in candidate_name.values():
                if len(name) and name in google_json['item_title']:
                    likelihood_score += 5
                    name_found_in_title = True
                if len(name) and (name in google_json['item_snippet'].lower() or
                                  name in google_json['item_meta_tags_description'].lower()):
                    likelihood_score += 5
                    name_found_in_description = True
            # If candidate_name is not present in title, snippet and description then skip this search entry
            if not name_found_in_title and not name_found_in_description:
                continue
            if not name_found_in_title:
                likelihood_score -= 20
            if not name_found_in_description:
                likelihood_score -= 10

            if "ballotpedia" in google_json['item_link']:
                likelihood_score += 70
            if "linkedin" in google_json['item_link']:
                likelihood_score += 50
            if "facebook" in google_json['item_link']:
                likelihood_score += 40

            # Check (each word individually) if office name is in description
            # This also checks if state code is in description
            office_name = candidate_campaign.contest_office_name
            if positive_value_exists(office_name) and (google_json['item_snippet'] or
                                                       google_json['item_meta_tags_description']):
                office_name = office_name.lower()
                office_name = office_name.split()
                office_found_in_description = False
                for word in office_name:
                    if len(word) > 1 and (word in google_json['item_snippet'].lower() or
                                          word in google_json['item_meta_tags_description'].lower()):
                        likelihood_score += 10
                        office_found_in_description = True
                if not office_found_in_description:
                    likelihood_score -= 10

            # Increase the score for every positive keyword we find
            for keyword in POSITIVE_KEYWORDS:
                if google_json['item_snippet'] and keyword in google_json['item_snippet'].lower() or \
                        google_json['item_meta_tags_description'] and \
                        keyword in google_json['item_meta_tags_description'].lower():
                    likelihood_score += 5

            # Decrease the score for every negative keyword we find
            for keyword in NEGATIVE_KEYWORDS:
                if (google_json['item_snippet'] and keyword in google_json['item_snippet'].lower()) or \
                    (google_json['item_meta_tags_description'] and
                     keyword in google_json['item_meta_tags_description'].lower()):
                    likelihood_score -= 20

            if likelihood_score < 0:
                continue

            current_candidate_google_search_info = {
                'search_term':              search_term,
                'likelihood_score':         likelihood_score,
                'google_json':              google_json
            }
            possible_google_search_users_list.append(current_candidate_google_search_info)
    return possible_google_search_users_list


def parse_google_search_results(search_term, result):
    search_request_url = "https://www.googleapis.com/customsearch/v1?q={search_term}&" \
                         "cx={google_search_engine_id}&filter=1&fl=countryUS&key={google_search_api_key}". \
        format(search_term=search_term, google_search_engine_id=GOOGLE_SEARCH_ENGINE_ID,
               google_search_api_key=GOOGLE_SEARCH_API_KEY)

    item_title = result['title'] if 'title' in result else ''
    item_link = result['link'] if 'link' in result else ''
    item_snippet = result['snippet'] if 'snippet' in result else ''
    item_formatted_url = result['formattedUrl'] if 'formattedUrl' in result else ''
    item_image = (result.get('pagemap').get('metatags')[0].get('og:image')
                  if 'pagemap' in result and result.get('pagemap', {}).get('metatags', []) and
                  result.get('pagemap', {}).get('metatags', [])[0].get('og:image') else '')
    item_meta_tags_description = (result.get('pagemap').get('metatags')[0].get('og:description')
                                  if 'pagemap' in result and result.get('pagemap', {}).get('metatags', []) and
                                  result.get('pagemap', {}).get('metatags', [])[0].get('og:description') else '')
    item_formatted_url = (item_formatted_url
                          if item_link.split("//")[-1] != item_formatted_url.split("//")[-1] else "")
    item_person_location = (result.get('pagemap').get('person')[0].get('location')
                            if 'pagemap' in result and result.get('pagemap', {}).get('person', []) and
                            result.get('pagemap', {}).get('person', [])[0].get('location', {}) else None)
    google_json = {
        'item_title':                   item_title,
        'item_link':                    item_link,
        'item_snippet':                 item_snippet,
        'item_image':                   item_image,
        'item_formatted_url':           item_formatted_url,
        'item_meta_tags_description':   item_meta_tags_description,
        'item_person_location':         item_person_location,
        'search_request_url':           search_request_url,
    }
    return google_json


def retrieve_possible_wikipedia_page(search_term):
    status = ""
    wikipedia_results = reach_out_to_wikipedia_with_guess(search_term, auto_suggest=False, preload=True)
    page_found = wikipedia_results['page_found']
    wikipedia_page = wikipedia_results['wikipedia_page']
    if not page_found:
        # search with auto_suggest as True (auto_suggest: If the literal string isn't found, try another page)
        wikipedia_auto_suggest_results = reach_out_to_wikipedia_with_guess(search_term, auto_suggest=True, preload=True)
        page_found = wikipedia_auto_suggest_results['page_found']
        wikipedia_page = wikipedia_auto_suggest_results['wikipedia_page']

    if page_found:
        status += "RETRIEVED_CANDIDATE_WIKIPEDIA_RESULTS"
    else:
        status += "RETRIEVE_CANDIDATE_WIKIPEDIA_RESULTS_FALIED"

    results = {
        'status':                  status,
        'wikipedia_page_found':    page_found,
        'wikipedia_page':          wikipedia_page,
    }
    return results


def update_google_search_with_wikipedia_results(wikipedia_page, search_term, candidate_name, candidate_campaign,
                                                possible_google_search_users_list):
    wikipedia_user_exist_in_google_search = False
    possible_wikipedia_search_user = analyze_wikipedia_search_results(wikipedia_page, search_term, candidate_name,
                                                                      candidate_campaign)
    for google_search_user in possible_google_search_users_list:
        if wikipedia_page and wikipedia_page.url == google_search_user['google_json']['item_link']:
            wikipedia_user_exist_in_google_search = True
            possible_wikipedia_search_user = possible_wikipedia_search_user[0]
            google_search_user['likelihood_score'] = possible_wikipedia_search_user['likelihood_score']

    results = {
        'wikipedia_user_exist_in_google_search':    wikipedia_user_exist_in_google_search,
        'possible_wikipedia_search_user':           possible_wikipedia_search_user
    }
    return results


def analyze_wikipedia_search_results(wikipedia_page, search_term, candidate_name,
                                     candidate_campaign):
    likelihood_score = 10
    possible_google_search_users_list = []
    wikipedia_images_result = retrieve_candidate_images_from_wikipedia_page(candidate_campaign, wikipedia_page,
                                                                            force_retrieve=True)

    google_json = {
        'item_title':                   wikipedia_page.original_title,
        'item_link':                    wikipedia_page.url,
        'item_snippet':                 wikipedia_page.summary,
        'item_image':                   wikipedia_images_result['image'] if wikipedia_images_result['success'] else '',
        'item_formatted_url':           '',
        'item_meta_tags_description':   '',
        'item_person_location':         '',
        'search_request_url':           '',
    }

    # Check if name (or parts of name) are in title, snippet and description
    name_found_in_title = False
    name_found_in_description = False
    for name in candidate_name.values():
        if len(name) and name in google_json['item_title']:
            likelihood_score += 5
            name_found_in_title = True
        if len(name) and name in google_json['item_snippet'].lower():
            likelihood_score += 5
            name_found_in_description = True

    if not name_found_in_title and not name_found_in_description:
        return possible_google_search_users_list
    if not name_found_in_title:
        likelihood_score -= 20
    if not name_found_in_description:
        likelihood_score -= 10

    # Check (each word individually) if office name is in description
    # This also checks if state code is in description
    office_name = candidate_campaign.contest_office_name
    if positive_value_exists(office_name) and google_json['item_snippet']:
        office_name = office_name.lower()
        office_name = office_name.split()
        office_found_in_description = False
        for word in office_name:
            if len(word) > 1 and word in google_json['item_snippet'].lower():
                likelihood_score += 10
                office_found_in_description = True
        if not office_found_in_description:
            likelihood_score -= 10

    # Increase the score for every positive keyword we find
    for keyword in POSITIVE_KEYWORDS:
        if google_json['item_snippet'] and keyword in google_json['item_snippet'].lower():
            likelihood_score += 5

    # Decrease the score for every negative keyword we find
    for keyword in NEGATIVE_KEYWORDS:
        if google_json['item_snippet'] and keyword in google_json['item_snippet'].lower():
            likelihood_score -= 20

    if likelihood_score < 0:
        return possible_google_search_users_list

    current_candidate_wikipedia_search_info = {
        'search_term':              search_term,
        'likelihood_score':         likelihood_score,
        'google_json':              google_json
    }
    possible_google_search_users_list.append(current_candidate_wikipedia_search_info)
    return possible_google_search_users_list
