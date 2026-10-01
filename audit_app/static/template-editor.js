// Rich email editor: formatting, merge tags, and form serialization.
const editor = document.getElementById('body-editor');
const subject = document.getElementById('subject');
const hiddenBody = document.getElementById('body');
let lastMergeTarget = editor;

editor.addEventListener('focus', () => lastMergeTarget = editor);
subject.addEventListener('focus', () => lastMergeTarget = subject);

document.querySelectorAll('.format-button, .tag-button').forEach(button => {
  button.addEventListener('mousedown', event => event.preventDefault());
});

document.querySelectorAll('.format-button').forEach(button => {
  button.addEventListener('click', () => {
    editor.focus();
    document.execCommand(button.dataset.command, false, null);
  });
});

document.querySelectorAll('.tag-button').forEach(button => {
  button.addEventListener('click', () => {
    const tag = button.dataset.tag;
    if (lastMergeTarget === subject) {
      const start = subject.selectionStart;
      const end = subject.selectionEnd;
      subject.value = subject.value.slice(0, start) + tag + subject.value.slice(end);
      subject.focus();
      subject.setSelectionRange(start + tag.length, start + tag.length);
    } else {
      editor.focus();
      document.execCommand('insertText', false, tag);
    }
  });
});

document.querySelector('.template-form').addEventListener('submit', () => {
  hiddenBody.value = editor.innerHTML;
});
