import re

from launchpyd.lp_types import DiffPerFileInfoType


def extract_file_and_line_from_diff(line_number, diff_txt) -> tuple[str, int, str]:
    """
    Extracts the file path, relative line number, and line content from a diff for a given line number.

    Args:
        line_number (int): The line number to extract from the diff.
        diff_txt (str): The diff text to extract from.

    Returns:
        tuple: A tuple containing the file path (str), relative line number (int), and line content (str).
    """
    # Split the diff into lines
    lines = diff_txt.split("\n")

    current_file = None
    current_line_number_in_file = 0

    added_lines = 0  # Track the number of lines added in the diff
    removed_lines = 0  # Track the number of lines removed in the diff

    for i, line in enumerate(lines):
        # Detect a file path in the diff
        if line.startswith("+++ "):
            current_file = line.split(" ")[1][2:]

        elif line.startswith("@@"):
            # Parse chunk header to get starting line number in the file
            _, chunk_info = line.split("@@", 1)
            current_line_number_in_file = int(chunk_info.split(" ")[2].split(",")[0])

            # Reset added and removed line counters for each new chunk
            added_lines = 0
            removed_lines = 0

            # Adjust line number for chunks starting from 0 (new files)
            if current_line_number_in_file == 0:
                current_line_number_in_file = 1
            continue

        # If the current line number in the diff matches the provided line_number
        if i + 1 == line_number:
            # print(f"Diff line: {line}")
            return current_file, current_line_number_in_file + added_lines - removed_lines, line

        # Adjust line count based on whether lines are added, removed, or unchanged
        if line.startswith("+"):
            added_lines += 1
        elif line.startswith("-"):
            removed_lines += 1
        else:
            # This accounts for unchanged lines which should also increase the current line number in file
            current_line_number_in_file += 1

    # If the line number was not found in the diff
    return None, None, None


# for each entry in a comments.json file,
# add a new key-value pair to the entry with the file and relative line number
# then write the updated comments to a new json file
def match_diff_comments_with_file(comments, diff_txt):
    """
    For each entry in a comments.json file, add new key-value pairs to the entry with the file and relative line number, then return the updated comments.
    """
    new_comments = []
    for comment in comments:
        new_comment = comment.copy()
        file, relative_line, line_content = extract_file_and_line_from_diff(
            line_number=comment["diff_line_no"], diff_txt=diff_txt
        )
        new_comment["file"] = file
        del new_comment["diff_line_no"]
        new_comment["line_no"] = relative_line
        new_comments.append(new_comment)
    return new_comments


def construct_git_ssh_url(git_repository_link):
    """
    Construct a git ssh url from a git repository link.
    """
    # make sure the url is in the format "https://api.launchpad.net/devel/~virtustom/cloudware/+git/cpc_jenkins"
    # use regex
    import re

    prefix = "https://api.launchpad.net/devel/"
    if not git_repository_link.startswith(prefix):
        raise ValueError(f"git_repository_link should start with {prefix}")
    new_prefix = "git+ssh://git.launchpad.net/"
    new_git_repository_link = git_repository_link.replace(prefix, new_prefix)
    return new_git_repository_link


def parse_base_diff_per_file_info(diff_text: str) -> list[DiffPerFileInfoType]:
    # Split the diff into lines
    lines = diff_text.split("\n")

    # Regular expressions to match different parts of the diff
    diff_start_regex = re.compile(r"^diff --git a/(.+) b/(.+)$")
    file_status_regex = re.compile(r"^(new file|deleted file)")

    # Store the parsed data
    parsed_data = []
    current_file = {}
    counting = False

    for line in lines:
        # Check for start of a new file diff
        diff_start_match = diff_start_regex.match(line)
        if diff_start_match:
            # Save previous file data if exists
            if current_file:
                parsed_data.append(DiffPerFileInfoType(**current_file))

            # Reset for new file
            current_file = {
                "file": diff_start_match.group(1),
                "lines_added": 0,
                "lines_deleted": 0,
                "status": "modified",  # default status
            }
            counting = False
            continue

        # Check for file status (new, deleted)
        file_status_match = file_status_regex.match(line)
        if file_status_match:
            current_file["status"] = file_status_match.group(1)
            continue

        # Start counting lines after the file header
        if line.startswith("+++") or line.startswith("---"):
            counting = True
            continue

        # Count added and deleted lines
        if counting:
            if line.startswith("+") and not line.startswith("+++"):
                current_file["lines_added"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                current_file["lines_deleted"] += 1

    # Add the last file data if exists
    if current_file:
        parsed_data.append(DiffPerFileInfoType(**current_file))

    return parsed_data
