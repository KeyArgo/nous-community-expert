#!/usr/bin/env python3
"""
Parse Nous Discord archive files into structured JSON chunks.
Output: chunks.jsonl — one JSON object per conversation chunk.
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime

MSG_PATTERN = re.compile(
    r'^\[(\d{4}-\d{2}-\d{2}T[\d:.]+\+\d{2}:\d{2})\] (.+?) \(id=(\d+)\)'
)
REPLY_PATTERN = re.compile(r'\[reply to (.+?) msg=(\d+)\]')
EMBED_PATTERN = re.compile(r'\[embed\] (.+)')
ATTACHMENT_PATTERN = re.compile(r'\[attachment\] (.+?)(?:\s+https?://\S+)?$')


def parse_channel_file(filepath: Path, channel_name: str) -> list:
    """Parse a channel text file into message dicts."""
    messages = []
    current_msg = None

    with open(filepath) as f:
        for line in f:
            line = line.rstrip('\n')

            # Skip header lines
            if line.startswith('#'):
                continue

            m = MSG_PATTERN.match(line)
            if m:
                if current_msg:
                    messages.append(current_msg)
                current_msg = {
                    'timestamp': m.group(1),
                    'author': m.group(2),
                    'message_id': m.group(3),
                    'content': '',
                    'channel': channel_name,
                    'reply_to': None,
                    'embeds': [],
                    'attachments': [],
                }
                continue

            if current_msg is not None:
                stripped = line.strip()
                if not stripped:
                    continue

                # Check for reply
                reply_m = REPLY_PATTERN.search(stripped)
                if reply_m:
                    current_msg['reply_to'] = reply_m.group(2)
                    stripped = REPLY_PATTERN.sub('', stripped).strip()

                # Check for embed
                embed_m = EMBED_PATTERN.search(stripped)
                if embed_m:
                    current_msg['embeds'].append(embed_m.group(1))
                    stripped = ''
                    continue

                # Check for attachment
                att_m = ATTACHMENT_PATTERN.match(stripped)
                if att_m:
                    current_msg['attachments'].append(stripped)
                    continue

                if stripped:
                    if current_msg['content']:
                        current_msg['content'] += '\n' + stripped
                    else:
                        current_msg['content'] = stripped

        if current_msg:
            messages.append(current_msg)

    return messages


def chunk_messages(messages: list, max_chunk_size: int = 1500) -> list:
    """
    Group messages into conversation chunks.
    New chunk when: silence > 30 min, topic change, or size limit.
    """
    if not messages:
        return []

    chunks = []
    current_chunk = {
        'channel': messages[0]['channel'],
        'messages': [],
        'start_time': messages[0]['timestamp'],
        'end_time': messages[0]['timestamp'],
        'authors': set(),
        'text': '',
    }

    prev_time = datetime.fromisoformat(messages[0]['timestamp'])

    for msg in messages:
        msg_time = datetime.fromisoformat(msg['timestamp'])
        time_gap = (msg_time - prev_time).total_seconds()

        # Estimate chunk text size
        chunk_text_size = len(current_chunk['text']) + len(msg.get('content', ''))

        # Start new chunk on: 30min silence, or oversized chunk
        start_new = False
        if time_gap > 1800:  # 30 minutes
            start_new = True
        elif chunk_text_size > max_chunk_size and current_chunk['messages']:
            start_new = True

        if start_new and current_chunk['messages']:
            current_chunk['authors'] = list(current_chunk['authors'])
            chunks.append(current_chunk)
            current_chunk = {
                'channel': msg['channel'],
                'messages': [],
                'start_time': msg['timestamp'],
                'end_time': msg['timestamp'],
                'authors': set(),
                'text': '',
            }

        # Add message to chunk
        author = msg['author']
        content = msg.get('content', '')
        if content:
            current_chunk['text'] += f"[{author}]: {content}\n"
        current_chunk['messages'].append({
            'timestamp': msg['timestamp'],
            'author': author,
            'message_id': msg['message_id'],
            'content': content,
            'reply_to': msg.get('reply_to'),
            'embeds': msg.get('embeds', []),
            'attachments': msg.get('attachments', []),
        })
        current_chunk['authors'].add(author)
        current_chunk['end_time'] = msg['timestamp']
        prev_time = msg_time

    if current_chunk['messages']:
        current_chunk['authors'] = list(current_chunk['authors'])
        chunks.append(current_chunk)

    return chunks


def parse_forum_file(filepath: Path) -> dict:
    """Parse a forum thread file into a structured thread object."""
    messages = []
    thread_title = filepath.stem.split('-', 1)[-1] if '-' in filepath.stem else filepath.stem
    thread_id = filepath.stem.split('-')[0] if '-' in filepath.stem else ''
    channel_name = filepath.parent.name

    with open(filepath) as f:
        for line in f:
            line = line.rstrip('\n')
            if line.startswith('#'):
                # Extract thread title from header
                if 'Thread' in line:
                    title_m = re.search(r"Thread '(.+?)'", line)
                    if title_m:
                        thread_title = title_m.group(1)
                continue

            m = MSG_PATTERN.match(line)
            if m:
                messages.append({
                    'timestamp': m.group(1),
                    'author': m.group(2),
                    'message_id': m.group(3),
                })
                continue

            if messages:
                last = messages[-1]
                stripped = line.strip()
                if stripped:
                    last.setdefault('content', '')
                    if last['content']:
                        last['content'] += '\n' + stripped
                    else:
                        last['content'] = stripped

    return {
        'type': 'forum_thread',
        'thread_id': thread_id,
        'thread_title': thread_title,
        'channel': channel_name,
        'message_count': len(messages),
        'messages': messages,
    }


def main():
    import sys
    archive_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('archives')
    output_file = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('tools/chunks.jsonl')

    all_chunks = []

    # Parse channel text files
    for txt_file in sorted(archive_dir.glob('*.txt')):
        channel_name = txt_file.stem
        print(f"Parsing {txt_file.name}...")
        messages = parse_channel_file(txt_file, channel_name)
        print(f"  {len(messages)} messages")
        chunks = chunk_messages(messages)
        print(f"  {len(chunks)} chunks")
        for chunk in chunks:
            chunk['type'] = 'channel_chunk'
            chunk['id'] = f"{chunk['channel']}_{chunk['start_time']}"
            all_chunks.append(chunk)

    # Parse forum threads
    for forum_dir in sorted(archive_dir.iterdir()):
        if forum_dir.is_dir():
            for thread_file in sorted(forum_dir.glob('*.txt')):
                thread = parse_forum_file(thread_file)
                # Create one chunk per thread
                chunk_text = '\n'.join(
                    f"[{m.get('author', 'unknown')}]: {m.get('content', '')}"
                    for m in thread['messages'] if m.get('content')
                )
                all_chunks.append({
                    'type': 'forum_chunk',
                    'id': f"{thread['channel']}_{thread['thread_id']}",
                    'channel': thread['channel'],
                    'thread_title': thread['thread_title'],
                    'thread_id': thread['thread_id'],
                    'start_time': thread['messages'][0]['timestamp'] if thread['messages'] else '',
                    'end_time': thread['messages'][-1]['timestamp'] if thread['messages'] else '',
                    'authors': list(set(m.get('author', '') for m in thread['messages'])),
                    'text': chunk_text,
                    'message_count': thread['message_count'],
                })

    # Write output
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w') as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk) + '\n')

    print(f"\nTotal chunks: {len(all_chunks)}")
    print(f"Output: {output_file}")

    # Stats
    channel_chunks = [c for c in all_chunks if c['type'] == 'channel_chunk']
    forum_chunks = [c for c in all_chunks if c['type'] == 'forum_chunk']
    print(f"Channel chunks: {len(channel_chunks)}")
    print(f"Forum chunks: {len(forum_chunks)}")

    total_text = sum(len(c.get('text', '')) for c in all_chunks)
    print(f"Total text size: {total_text / 1024 / 1024:.1f} MB")


if __name__ == '__main__':
    main()
