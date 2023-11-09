from launchpadlib.launchpad import Launchpad
from pprint import pprint
import re
from launchpyd.lp_utils import *
from launchpyd.lp_types import *
from datetime import datetime

LP = None

def login():
    global LP
    print("Logging into Launchpad...")
    launchpad = Launchpad.login_with("py-launchpad", 'production', version="devel")
    LP = launchpad
    print("Logged in as: " + str(LP.me))
    return launchpad

def debug(obj):
    print("lp_attributues:",obj.lp_attributes)
    print("lp_operations:",obj.lp_operations)
    print("lp_collections:",obj.lp_collections)
    print("lp_entries:",obj.lp_entries)

def find_latest_matching_entry(data_list, target_date):
    # Convert the target date string into a date object
    target_date_obj = datetime.strptime(target_date, "%m/%d/%Y").date()
    
    # Filter the list for entries with the same date as the target date
    matching_entries = [
        entry for entry in data_list 
        if datetime.fromisoformat(entry['date_created']).date() == target_date_obj
    ]
    
    # Return the latest entry if there are any matches, or None otherwise
    if matching_entries:
        return max(matching_entries, key=lambda x: x['date_created'])
    return None

def parse_repo_owner_from_url(url):
    username = re.findall(r"launchpad.net/.*~([0-9 a-z A-Z _ -]+)/", url)[0]
    return username

def parse_project_name_from_url(url):
    project_name = re.findall(r"launchpad.net/.*~[0-9 a-z A-Z _ -]+/([0-9 a-z A-Z _ -]+)", url)[0]
    return project_name

def parse_repo_name_from_url(url):
    repo_name = re.findall(r"launchpad.net/.*~[0-9 a-z A-Z _ -]+/[0-9 a-z A-Z _ -]+/\+git/([0-9 a-z A-Z _ -]+)", url)[0]
    return repo_name

def get_project(project_name: str):
    cw = LP.projects[project_name]
    return cw

def get_mps_from_lp_project(project_name: str):
    proj = get_project(project_name)
    mps = proj.getMergeProposals().entries
    return mps

def get_mp_lp_obj_from_url(url):
    project_name = parse_project_name_from_url(url)
    mps = get_mps_from_lp_project(project_name)
    for mp in mps:
        if mp["web_link"] == url:
            return LP.load(mp["self_link"])
    return None

def get_diff_stats(diff):
    if isinstance(diff, dict):
        diff_stat_dict = diff["diffstat"]
    else:
        diff_stat_dict = diff.diffstat
    diff_stats = [
        {
            "file": file,
            "additions": int(add_del[0]),
            "deletions": int(add_del[1]),
        }
        for file, add_del in diff_stat_dict.items()
    ]
    return diff_stats

def get_diffs_from_mp_url(mp_url):
    mp = get_mp_lp_obj_from_url(mp_url)
    diffs = mp.preview_diffs.entries
    return diffs

def convert_inline_comments_dict_to_type(inline_comment : dict):
    return InlineCommentType (
            file=inline_comment["file"],
            line_no=inline_comment["line_no"],
            messages=[
                InlineCommentMessageType(
                    author_username=message["author_username"],
                    author_display_name=message["author_display_name"],
                    message=message["message"],
                    date=message["date"],
                )
                for message in inline_comment["messages"]
            ]
        )

def get_diff_inline_comments_and_text_for_mp_and_diff(mp_obj, diff_obj):
    preview_diff_id = diff_obj.id
    inline_comments = mp_obj.getInlineComments(previewdiff_id=preview_diff_id)
    # Transform the inline comments
    simplified_comments = [
        {
            'diff_line_no': int(comment['line_number']),
            "messages": [{
                'author_username': comment['person']['name'],
                'author_display_name': comment['person']['display_name'],
                'message': comment['text'],
                'date': comment['date'],
            }]
        }
        for comment in inline_comments
    ]
    # merge all comments that occur on the same line into one comment
    # and merge the messages into one list
    for i in range(len(simplified_comments)):
        for j in range(i+1, len(simplified_comments)):
            if simplified_comments[i]['diff_line_no'] == simplified_comments[j]['diff_line_no']:
                simplified_comments[i]['messages'].extend(simplified_comments[j]['messages'])
                simplified_comments.pop(j)
                break
    # read in the diff text
    diff_text_obj = diff_obj.diff_text
    with diff_text_obj.open("r") as diff_file:
        diff_txt: str = diff_file.read().decode("utf-8")
    inline_comments = match_diff_comments_with_file(simplified_comments, diff_txt)
    return inline_comments, diff_txt

def get_diffs_from_mp_url(url) -> list[DiffType]:
    mp_obj = get_mp_lp_obj_from_url(url)
    diffs: list[DiffType] = []
    for diff in mp_obj.preview_diffs.entries:
        # pprint(diff)
        diff_obj = LP.load(diff["self_link"])
        inline_comments_dicts, diff_text = get_diff_inline_comments_and_text_for_mp_and_diff(mp_obj, diff_obj)
        diff_stats = get_diff_stats(diff_obj)
        diffs.append(
            DiffType (
                diff_stats=diff_stats,
                inline_comments= [
                    convert_inline_comments_dict_to_type(d) for d in inline_comments_dicts
                ],
                id=diff_obj.id,
                self_link=diff_obj.self_link,
                diff_text=diff_text
            )
        )
    return diffs

def parse_source_and_target_info(mp_dict: dict) -> dict:
    """
    Will return source_branch, target_branch, source_owner, target_owner, source_git_url, target_git_url as a dict
    """
    # mp_dict[""] = refs/heads/*branch_name*
    source_branch = mp_dict["source_git_path"].split("/")[-1]
    target_branch = mp_dict["target_git_path"].split("/")[-1]
    source_owner = parse_repo_owner_from_url(mp_dict["source_git_repository_link"])
    target_owner = parse_repo_owner_from_url(mp_dict["target_git_repository_link"])
    return {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "source_owner": source_owner,
        "target_owner": target_owner,
        "source_git_url": f"https://git.launchpad.net/~{source_owner}/{mp_dict['source_git_path']}",
        "target_git_url": f"https://git.launchpad.net/~{target_owner}/{mp_dict['target_git_path']}",
    }

def parse_jira_tickets_from_mp(mp: MergeProposalType, jira_prefixes: list[str]) -> list[str]:
    def find_jira_ticket_mentions(texts: list[str]) -> str:
        results = []
        for text in texts:
            if text is None:
                continue
            for prefix in jira_prefixes:
                matches = re.findall(rf"({prefix.upper()}-\d+)", text.upper())
                if len(matches) > 0:
                    results += [m.upper() for m in matches]
        return results

    jira_tickets = find_jira_ticket_mentions([mp.description, mp.commit_message, mp.source_branch])
    return jira_tickets

def convert_mp_dict_to_type(mp_dict: dict) -> MergeProposalType:
    source_and_target_info = parse_source_and_target_info(mp_dict)
    mp_type = MergeProposalType(
        id=mp_dict["web_link"].split("/")[-1],
        self_link=mp_dict["self_link"],
        repo_name=parse_repo_name_from_url(mp_dict["web_link"]),
        url=mp_dict["web_link"],
        review_state=mp_dict["queue_status"],
        diffs=[],
        description=mp_dict["description"],
        commit_message=mp_dict["commit_message"],
        ci_cd_status=get_mp_ci_cd_state(mp_url=mp_dict["web_link"]),
        jira_tickets=[],
        comments=get_mp_comments(mp_url=mp_dict["web_link"]),
        review_votes=get_review_votes(mp_url=mp_dict["web_link"]),
        **source_and_target_info,
    )
    return mp_type

def convert_mps(mps: list[dict], fetch_diffs: bool) -> list[MergeProposalType]:
    mp_types = []
    for mp in mps:
        mp_type = convert_mp_dict_to_type(mp)
        if fetch_diffs:
            mp_type.diffs = get_diffs_from_mp_url(mp_type.url)
        mp_types.append(mp_type)
    return mp_types

def get_all_mps_from_user(username: str = None, fetch_diffs: bool = False):
    if username is None:
        user = LP.me
    else:    
        user = LP.people[username]
    mps = user.getMergeProposals().entries
    return convert_mps(mps, fetch_diffs=fetch_diffs)

def get_all_mps_from_project(project_name: str, fetch_diffs: bool = False):
    proj = get_project(project_name)
    mps = proj.getMergeProposals().entries
    return convert_mps(mps, fetch_diffs=fetch_diffs)

def get_mp_comments(mp_url: str) -> list[MergeProposalCommentType]:
    mp = get_mp_lp_obj_from_url(mp_url)
    results = []
    for comment in mp.all_comments.entries:
        results.append(
            MergeProposalCommentType(
                id=comment["id"],
                self_link=comment["self_link"],
                message=comment["message_body"],
                author_username=comment["author_link"].split("/~")[-1],
                date_created=comment["date_created"],
                date_last_edited=comment["date_last_edited"]
            )
        )
    return results

def get_mp_ci_cd_state(mp_url: str):
    comments = get_mp_comments(mp_url=mp_url)
    comments.reverse()
    for comment in comments:
        if "PASSED: Continuous integration" in comment.message:
            return "PASSING"
        elif "FAILED: Continuous integration" in comment.message:
            return "FAILING"
    return "UNKNOWN"

def get_review_votes(mp_url: str):
    mp = get_mp_lp_obj_from_url(mp_url)
    votes = mp.votes.entries
    reviews: list[MergeProposalReviewVote] = []
    for vote_entry in votes:
        vote = LP.load(vote_entry["self_link"])
        if vote.comment and vote.comment.vote_tag == "continuous-integration":
            continue
        reviewer_needed = vote.is_pending
        reviews.append(
            MergeProposalReviewVote(
                reviewer_username=vote.reviewer.name,
                reviewer_display_name=vote.reviewer.display_name,
                vote=str(vote.comment.vote).upper() if vote.comment else None,
                needs_reviewer=reviewer_needed,
            )
        )
    return reviews

if __name__ == '__main__':
    print("No functionality is provided by this module. Please invoke via cli.")
    
    
    