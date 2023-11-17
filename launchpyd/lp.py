import os
import re
import shutil
import subprocess
from datetime import datetime
from pprint import pprint

from launchpadlib.launchpad import Launchpad

from launchpyd.lp_types import *
from launchpyd.lp_utils import *

LP = None


def login():
    global LP
    print("Logging into Launchpad...")
    launchpad = Launchpad.login_with("py-launchpad", "production", version="devel")
    LP = launchpad
    print("Logged in as: " + str(LP.me))
    return launchpad


def debug(obj):
    print("lp_attributues:", obj.lp_attributes)
    print("lp_operations:", obj.lp_operations)
    print("lp_collections:", obj.lp_collections)
    print("lp_entries:", obj.lp_entries)


def find_latest_matching_entry(data_list, target_date):
    # Convert the target date string into a date object
    target_date_obj = datetime.strptime(target_date, "%m/%d/%Y").date()

    # Filter the list for entries with the same date as the target date
    matching_entries = [
        entry for entry in data_list if datetime.fromisoformat(entry["date_created"]).date() == target_date_obj
    ]

    # Return the latest entry if there are any matches, or None otherwise
    if matching_entries:
        return max(matching_entries, key=lambda x: x["date_created"])
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

# CACHE = {"lp_mp_objs": {}}

def get_lp_mp_obj_from_url(url):
    project_name = parse_project_name_from_url(url)
    mps = get_mps_from_lp_project(project_name)
    for mp in mps:
        if mp["web_link"] == url:
            return LP.load(mp["self_link"])
    return None


def get_diff_stats(diff_dict: dict) -> list[DiffStatType]:
    diff_stat_dict = diff_dict["diffstat"]
    diff_stats = [
        DiffStatType(
            file=file,
            additions=int(add_del[0]),
            deletions=int(add_del[1]),
        )
        for file, add_del in diff_stat_dict.items()
    ]
    return diff_stats


def convert_inline_comments_dict_to_type(inline_comment: dict):
    return InlineCommentType(
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
        ],
    )


def get_diff_inline_comments_and_text_for_mp_and_diff(mp_obj, diff_obj):
    preview_diff_id = diff_obj.id
    inline_comments = mp_obj.getInlineComments(previewdiff_id=preview_diff_id)
    # Transform the inline comments
    simplified_comments = [
        {
            "diff_line_no": int(comment["line_number"]),
            "messages": [
                {
                    "author_username": comment["person"]["name"],
                    "author_display_name": comment["person"]["display_name"],
                    "message": comment["text"],
                    "date": comment["date"],
                }
            ],
        }
        for comment in inline_comments
    ]
    # merge all comments that occur on the same line into one comment
    # and merge the messages into one list
    for i in range(len(simplified_comments)):
        for j in range(i + 1, len(simplified_comments)):
            if simplified_comments[i]["diff_line_no"] == simplified_comments[j]["diff_line_no"]:
                simplified_comments[i]["messages"].extend(simplified_comments[j]["messages"])
                simplified_comments.pop(j)
                break
    # read in the diff text
    diff_text_obj = diff_obj.diff_text
    with diff_text_obj.open("r") as diff_file:
        diff_txt: str = diff_file.read().decode("utf-8")
    inline_comments = match_diff_comments_with_file(simplified_comments, diff_txt)
    return inline_comments, diff_txt


def get_diffs_from_mp(lp_mp_obj=None, web_link:str=None) -> list[DiffType]:
    if lp_mp_obj is None:
        lp_mp_obj = get_lp_mp_obj_from_url(web_link)
    diffs: list[DiffType] = []
    for diff in lp_mp_obj.preview_diffs.entries:
        diff_obj = LP.load(diff["self_link"])
        inline_comments_dicts, diff_text = get_diff_inline_comments_and_text_for_mp_and_diff(lp_mp_obj, diff_obj)
        diff_stats = get_diff_stats(diff)
        pprint(diff)
        diffs.append(
            DiffType(
                diff_stats=diff_stats,
                inline_comments=[convert_inline_comments_dict_to_type(d) for d in inline_comments_dicts],
                id=diff["id"],
                self_link=diff["self_link"],
                diff_text=diff_text,
                title=diff["title"],
                source_revision_id=diff["source_revision_id"],
                target_revision_id=diff["target_revision_id"],
                original_file_contents=get_file_contents_from_git_url_and_hash(
                    target_git_url=construct_git_ssh_url(lp_mp_obj.target_git_repository_link),
                    target_branch=lp_mp_obj.target_git_path.split("/")[-1],
                    target_hash=diff["target_revision_id"],
                    relevant_files=[d.file for d in diff_stats],
                ),
            )
        )
    return diffs


def parse_source_and_target_info(lp_mp_obj: dict) -> dict:
    """
    Will return source_branch, target_branch, source_owner, target_owner, source_git_url, target_git_url as a dict
    """
    # lp_mp_obj."= refs/heads/*branch_name*
    source_branch = lp_mp_obj.source_git_path.split("/")[-1]
    target_branch = lp_mp_obj.target_git_path.split("/")[-1]
    source_owner = parse_repo_owner_from_url(lp_mp_obj.source_git_repository_link)
    target_owner = parse_repo_owner_from_url(lp_mp_obj.target_git_repository_link)
    return {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "source_owner": source_owner,
        "target_owner": target_owner,
        "source_git_url": f"https://git.launchpad.net/~{source_owner}/{lp_mp_obj.source_git_path}",
        "target_git_url": f"https://git.launchpad.net/~{target_owner}/{lp_mp_obj.target_git_path}",
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

def make_lpy_mp(web_link:str=None, lp_mp_obj = None, lp_mp_dict: dict = None, fetch_diffs=False) -> MergeProposalType:
    if lp_mp_obj is None:
        if not web_link:
            if not lp_mp_dict:
                raise ValueError("Must provide either web_link or lp_mp_obj or lp_mp_dict")
            web_link = lp_mp_dict["web_link"]
        lp_mp_obj = get_lp_mp_obj_from_url(web_link)
    source_and_target_info = parse_source_and_target_info(lp_mp_obj)
    lpy_mp = MergeProposalType(
        id=lp_mp_obj.web_link.split("/")[-1],
        self_link=lp_mp_obj.self_link,
        repo_name=parse_repo_name_from_url(lp_mp_obj.web_link),
        url=lp_mp_obj.web_link,
        review_state=lp_mp_obj.queue_status,
        diffs=[],
        description=lp_mp_obj.description,
        commit_message=lp_mp_obj.commit_message,
        ci_cd_status=get_mp_ci_cd_state(mp_url=lp_mp_obj.web_link),
        jira_tickets=[],
        comments=get_mp_comments(mp_url=lp_mp_obj.web_link),
        review_votes=get_review_votes(mp_url=lp_mp_obj.web_link),
        **source_and_target_info,
    )
    if fetch_diffs:
        lpy_mp.diffs = get_diffs_from_mp(lp_mp_obj=lp_mp_obj)
    return lpy_mp

def convert_mps(mps: list[dict], fetch_diffs: bool) -> list[MergeProposalType]:
    lpy_mps = []
    for mp in mps:
        make_lpy_mp(mp, fetch_diffs=fetch_diffs)
    return lpy_mps


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

def get_lpy_mp_from_url(url, fetch_diffs: bool = False):
    return make_lpy_mp(web_link=url, fetch_diffs=fetch_diffs)

def get_mp_comments(mp_url: str) -> list[MergeProposalCommentType]:
    mp = get_lp_mp_obj_from_url(mp_url)
    results = []
    for comment in mp.all_comments.entries:
        results.append(
            MergeProposalCommentType(
                id=comment["id"],
                self_link=comment["self_link"],
                message=comment["message_body"],
                author_username=comment["author_link"].split("/~")[-1],
                date_created=comment["date_created"],
                date_last_edited=comment["date_last_edited"],
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


def get_review_votes(mp_url: str, lp_mp_obj=None):
    if lp_mp_obj is None:
        lp_mp_obj = get_lp_mp_obj_from_url(mp_url)
    votes = lp_mp_obj.votes.entries
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

def get_all_repos(project_name:str):
    cw = get_project(project_name)
    pprint([entry["display_name"] for entry in cw.getBranches().entries])


# TODO: This does not work.
def get_file_contents_from_git_url_and_hash(target_git_url: str, target_branch:str, target_hash: str, relevant_files: list[str]) -> dict[str, str]:
    """
    Get the contents of the files in relevant_files from the git repo at git_url at the commit hash
    """
    print("Getting file contents from '{}' on branch '{}' at commit hash {}".format(target_git_url, target_branch, hash))
    # check if the target git url at the given branch and commit hash has already been cached in 

    # Create a temporary directory to clone the repository into
    temp_dir = "temp_git_clone"
    os.makedirs(temp_dir, exist_ok=True)
    
    # delete the temp directory if it already exists
    if os.path.exists(temp_dir):
        print("Path already exists")
        # check that the target git url is the origin remote 
        result = subprocess.run(f"git -C {temp_dir} remote -v", check=True, capture_output=True, shell=True, text=True)
        print(result.stdout)
        # if it is, then just pull the latest changes
        if target_git_url in result.stdout:
            checkout_cmd = f"git -C {temp_dir} checkout {target_branch}"
            subprocess.run(checkout_cmd.split(), check=True, shell=True)
            refresh_cmd = f"git -C {temp_dir} pull origin {target_branch}"
            subprocess.run(refresh_cmd.split(), check=True, shell=True)
        # if not, then delete the directory and clone the repo
        else:
            shutil.rmtree(temp_dir)
            # shutil.rmtree(temp_dir)
            os.makedirs(temp_dir, exist_ok=True)            
            # Clone the repository into the temporary directory
            clone_cmd = f"git clone -b {target_branch} {target_git_url} {temp_dir}"
            print(clone_cmd)
            subprocess.run(clone_cmd.split(), check=True)

    # Reset the repository to the specified commit hash
    reset_cmd = f"git -C {temp_dir} reset --hard {target_hash}"
    print(reset_cmd)
    subprocess.run(reset_cmd.split(), check=True)

    # Read in the contents of the relevant files
    file_contents = {}
    for file_path in relevant_files:
        # check if file exists
        full_path = os.path.join(temp_dir, file_path)
        if not os.path.exists(full_path):
            file_contents[file_path] = ""
        else:
            with open(full_path, "r") as f:
                file_contents[file_path] = f.read()
    breakpoint()
    return file_contents


if __name__ == "__main__":
    login()
    print("No functionality is provided by this module. Please invoke via cli.")
