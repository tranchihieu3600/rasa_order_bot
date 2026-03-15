Rasa voice-order bot (Vietnamese) - packaged for integration with WinForms app.

Instructions:

1. Cài đặt rasa:
   pip install rasa rasa-sdk pyodbc

2. nếu chưa có module thì train rasa
   rasa train --force

3. chạy action.py của rasa để thực hiện các công việc dựa theo ý định mà thực thi hàm (story.yml) và logic định sẳn có trong file action.
   rasa run actions --actions actions --debug

4. bật rasa shell
   rasa shell --debug

5. mở app C# và bạn đã có thể thực hiện gọi món bằng văn bản. Còn giọng nói(chỉ khi bạn có key api speech to text).
