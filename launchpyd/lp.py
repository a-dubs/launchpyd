import os
import pickle
import re
import shutil
import subprocess
from datetime import datetime
from pprint import pprint

from launchpadlib.launchpad import Launchpad
from tqdm import tqdm

from launchpyd.lp_types import *
from launchpyd.lp_utils import *

LP = None


def login():
    global LP
    print("Logging into Launchpad...")
    launchpad = Launchpad.login_with("py-launchpad", "production", version="devel")
    LP = launchpad
    print("Logged in as: " + str(LP.me.name))
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


def parse_repo_owner_from_url(url) -> str:
    username = re.findall(r"/.*~([0-9 a-z A-Z _ -]+)/", url)[0]
    return username


def parse_project_name_from_url(url):
    project_name = re.findall(r"/.*~[0-9 a-z A-Z _ -]+/([0-9 a-z A-Z _ -]+)", url)[0]
    return project_name


def parse_repo_name_from_url(url):
    repo_name = re.findall(r"/.*~[0-9 a-z A-Z _ -]+/[0-9 a-z A-Z _ -]+/\+git/([0-9 a-z A-Z _ -]+)", url)[0]
    return repo_name


def get_project(project_name: str):
    cw = LP.projects[project_name]
    return cw


def get_mps_from_lp_project(project_name: str):
    proj = get_project(project_name)
    mps = proj.getMergeProposals().entries
    return mps


def convert_web_link_to_api_link(web_link):
    return web_link.replace("code.launchpad.net", "api.launchpad.net/devel")


def get_lp_mp_obj_from_url(url):
    return LP.load(convert_web_link_to_api_link(url))
    project_name = parse_project_name_from_url(url)
    mps = get_mps_from_lp_project(project_name)
    for mp in mps:
        if mp["web_link"] == url:
            return LP.load(mp["self_link"])
    return None


def get_all_diff_per_file_info(lp_mp_obj, lp_diff_obj, diff_text) -> list[DiffPerFileInfoType]:
    try:
        target_revision_id = lp_diff_obj.target_revision_id

        diff_text_splits = {}

        for split in diff_text.split("diff --git")[1:]:
            split = "diff --git" + split
            filename_matches = re.findall(r"diff --git a/(.*) b/(.*)", split)[0]
            diff_text_splits[filename_matches[0]] = split

        original_file_contents = get_file_contents_from_git_url_and_hash(
            target_git_url=construct_git_ssh_url(lp_mp_obj.target_git_repository_link),
            target_branch=lp_mp_obj.target_git_path.split("/")[-1],
            target_hash=lp_diff_obj.target_revision_id,
            relevant_files=[filepath for filepath in diff_text_splits.keys()],
        )

        per_file_info_list = parse_base_diff_per_file_info(diff_text=diff_text)
        for file_info in per_file_info_list:
            file_info.original_file_contents = original_file_contents[file_info.file]
            file_info.diff_text_snippet = diff_text_splits[file_info.file]

        return per_file_info_list
    except Exception as e:
        raise e


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
                j -= 1  # decrement j to account for the removal of the element
                
                break
    # read in the diff text
    diff_text_obj = diff_obj.diff_text
    with diff_text_obj.open("r") as diff_file:
        diff_txt: str = diff_file.read().decode("utf-8")
    inline_comments = match_diff_comments_with_file(simplified_comments, diff_txt)
    return inline_comments, diff_txt

# TODO: add ability to request a single diff for a lpyd MergeProposalType object

def get_diffs_from_mp(
        num_diffs_to_fetch: int,
        fetch_diff_files: bool = False,
        lp_mp_obj=None, 
        web_link: str = None
    ) -> list[DiffType]:

    """
    Retrieves a list of diffs from the given lp_mp_obj or web_link.

    Args:
        num_diffs_to_fetch (int): The number of diffs to fetch. -1 will fetch all diffs.
        fetch_diff_files (bool, optional): Whether to fetch the diff files. Defaults to False. This is slow and should
        only be used when new diff files are needed.
        lp_mp_obj (LPMPType, optional): Can be provided to avoid fetching the lp_mp_obj from the web_link. Either
        lp_mp_obj or web_link must be provided.
        web_link (str, optional): The web link of the merge proposal. Either lp_mp_obj or web_link must be provided.
    Returns:
        list[DiffType]: A list of DiffType objects representing the fetched diffs.
    """
    if lp_mp_obj is None:
        lp_mp_obj = get_lp_mp_obj_from_url(web_link)
    diffs: list[DiffType] = []
    lp_diffs = [entry for entry in lp_mp_obj.preview_diffs.entries]
    lp_diffs.reverse()  # reverse the list so that the most recent diff is first
    for diff in lp_diffs:
        if num_diffs_to_fetch == 0:
            break
        diff_obj = LP.load(diff["self_link"])
        inline_comments_dicts, diff_text = get_diff_inline_comments_and_text_for_mp_and_diff(lp_mp_obj, diff_obj)
        diff_per_file_info = get_all_diff_per_file_info(lp_mp_obj, diff_obj, diff_text) if fetch_diff_files else []
        diffs.append(
            DiffType(
                inline_comments=[convert_inline_comments_dict_to_type(d) for d in inline_comments_dicts],
                id=diff["id"],
                self_link=diff["self_link"],
                diff_text=diff_text,
                title=diff["title"],
                date_created=diff["date_created"],
                source_revision_id=diff["source_revision_id"],
                target_revision_id=diff["target_revision_id"],
                diff_per_file_info=diff_per_file_info,
            )
        )
        num_diffs_to_fetch -= 1
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


def get_lpyd_mp(
    web_link: str = None,
    lp_mp_obj = None,
    lp_mp_dict: dict = None,
    num_diffs_to_fetch: int = 0,
    fetch_diff_files: bool = False,
) -> MergeProposalType:
    """
    Returns a MergeProposalType object
    """
    if lp_mp_obj is None:
        if not web_link:
            if not lp_mp_dict:
                raise ValueError("Must provide either web_link or lp_mp_obj or lp_mp_dict")
            web_link = lp_mp_dict["web_link"]
        lp_mp_obj = get_lp_mp_obj_from_url(web_link)
    source_and_target_info = parse_source_and_target_info(lp_mp_obj)
    lpyd_mp = MergeProposalType(
        id=lp_mp_obj.web_link.split("/")[-1],
        self_link=lp_mp_obj.self_link,
        repo_name=parse_repo_name_from_url(lp_mp_obj.web_link),
        url=lp_mp_obj.web_link,
        review_state=lp_mp_obj.queue_status,
        diffs=[],
        description=lp_mp_obj.description,
        commit_message=lp_mp_obj.commit_message,
        ci_cd_status=get_mp_ci_cd_state(mp_url=lp_mp_obj.web_link),
        comments=get_mp_comments(mp_url=lp_mp_obj.web_link),
        review_votes=get_review_votes(mp_url=lp_mp_obj.web_link),
        **source_and_target_info,
    )
    if num_diffs_to_fetch != 0:
        lpyd_mp.diffs = get_diffs_from_mp(
            lp_mp_obj=lp_mp_obj,
            num_diffs_to_fetch=num_diffs_to_fetch,
            fetch_diff_files=fetch_diff_files,
        )
    return lpyd_mp


def convert_lp_mps_to_lpyd_mps(mps: list[dict], **kwargs) -> list[MergeProposalType]:
    lpyd_mps = []
    for mp in tqdm(mps):
        lpyd_mps.append(get_lpyd_mp(lp_mp_dict=mp, **kwargs))
    return lpyd_mps


def get_all_mps_from_user(username: str = None, **kwargs):
    if username is None:
        user = LP.me
    else:
        user = LP.people[username]
    mps = user.getMergeProposals().entries
    return convert_lp_mps_to_lpyd_mps(mps, **kwargs)


def get_all_mps_from_project(project_name: str, **kwargs):
    proj = get_project(project_name)
    mps = proj.getMergeProposals().entries
    print("Found {} merge proposals".format(len(mps)))
    return convert_lp_mps_to_lpyd_mps(mps, **kwargs)


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


def get_all_repos(project_name: str):
    cw = get_project(project_name)
    pprint([entry["display_name"] for entry in cw.getBranches().entries])


def get_file_contents_from_git_url_and_hash(
    target_git_url: str, target_branch: str, target_hash: str, relevant_files: list[str]
) -> dict[str, str]:
    """
    Get the contents of the files in relevant_files from the git repo at git_url at the commit hash
    """
    print(
        "Getting file contents from '{}' on branch '{}' at commit hash {}".format(
            target_git_url, target_branch, target_hash
        )
    )
    repo_owner = parse_repo_owner_from_url(target_git_url)
    

    temp_dir = os.path.join(os.path.expanduser("~"), ".lpyd", "lp_git_cloning_dir", target_git_url.split("/")[-1])
    pickle_cache_dir = os.path.join(temp_dir+".lpyd_cache")
    pickle_path = os.path.join(pickle_cache_dir, f"{target_hash}-file-contents.pkl")
    # Create a temporary directory to clone the repository into
    if os.path.exists(temp_dir):
        # check if pickle already exists for this target_hash in the temp clone directory
        if os.path.exists(pickle_cache_dir):
            if os.path.exists(pickle_path):
                print("target git hash already exists in cache. using cached file contents.")
                with open(pickle_path, "rb") as f:
                    file_contents = pickle.load(f)
                return file_contents
        else:
            os.makedirs(pickle_cache_dir, exist_ok=True)
        result = subprocess.run(f"git -C {temp_dir} remote -v", check=True, capture_output=True, text=True, shell=True)
        if target_git_url not in result.stdout:
            remote_add_cmd = f"git -C {temp_dir} remote add {repo_owner} {target_git_url}"
            print(remote_add_cmd)
            subprocess.run(remote_add_cmd.split(), check=True, shell=True)

        # unsure if i need to call "remote update" - the once case I think this might have to happen is if new remote
        # branches are added to the repo since the last time git clone was called
        # remote_update_cmd = f"git -C {temp_dir} remote update {repo_owner}"
        # print(remote_update_cmd)
        # subprocess.run(remote_update_cmd.split(), check=True)
        checkout_cmd = f"git -C {temp_dir} checkout {target_branch}"
        print(checkout_cmd)
        subprocess.run(checkout_cmd.split(), check=True)
        # pull_cmd = f"git -C {temp_dir} pull {repo_owner} {target_branch}"
        # print(pull_cmd)
        # subprocess.run(pull_cmd.split(), check=True)
        fetch_cmd = f"git -C {temp_dir} fetch {repo_owner} {target_branch}"
        print(fetch_cmd)
        subprocess.run(fetch_cmd.split(), check=True)
    else:
        os.makedirs(temp_dir, exist_ok=True)
        shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)
        # Clone the repository into the temporary directory
        clone_cmd = f"git clone -b {target_branch} {target_git_url} {temp_dir}"
        print(clone_cmd)
        subprocess.run(clone_cmd.split(), check=True, stdout=subprocess.DEVNULL)
        # rename the origin remote to the repo owner
        remote_rename_cmd = f"git -C {temp_dir} remote rename origin {repo_owner}"
        print(remote_rename_cmd)
        subprocess.run(remote_rename_cmd.split(), check=True)

    # Reset the repository to the specified commit hash
    reset_cmd = f"git -C {temp_dir} reset --hard {target_hash}"
    # print(reset_cmd)
    subprocess.run(reset_cmd.split(), check=True, stdout=subprocess.DEVNULL)

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
    # save the file contents to a pickle
    with open(pickle_path, "wb") as f:
        pickle.dump(file_contents, f)
    return file_contents


if __name__ == "__main__":
    print("No functionality is provided by this module. Please invoke via cli.")
