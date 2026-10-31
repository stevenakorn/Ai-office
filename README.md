# AI 視覺辦公室（多 AI 版）

一句話派工給主管 Alfred，他會把任務拆給團隊；每位員工可以接不同家的 AI：

| 員工 | 職責 | 預設 AI |
|---|---|---|
| Alfred | 主管：拆任務、整合交付 | Claude Opus |
| Curry | 研究 | Gemini |
| Michel | 文案 | OpenAI |
| Mia | 設計（產出設計規格與出圖提示詞） | Claude Sonnet |
| Jackson | 剪輯（產出分鏡表） | OpenAI |
| John | 審查 | Claude Sonnet |

預設值都可以在網頁右上角「AI 設定」改，設定只存在你自己的瀏覽器裡。

```
office-ai/
├─ public/index.html   辦公室網頁（前端）
├─ api/index.py        Flask 後端：轉發到 Claude / OpenAI / Gemini / 任何 OpenAI 相容服務
├─ requirements.txt
├─ vercel.json
└─ .env.example        環境變數範本
```

## 部署到 Vercel

1. 在 GitHub 開一個新的 repo（建議設為 Private），把整個 `office-ai` 資料夾推上去。
2. 到 Vercel 選 **Add New → Project**，匯入這個 repo，Framework Preset 選 **Other**，按 Deploy。
3. 到專案的 **Settings → Environment Variables** 新增：
   - `OFFICE_TOKEN`：**一定要設**，自己想一組長密碼。沒設的話，任何拿到網址的人都能花你的 AI 額度。
   - `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`GEMINI_API_KEY`：有哪家填哪家，沒有的留空。
   - 選填：`CUSTOM_NAME`、`CUSTOM_BASE_URL`、`CUSTOM_API_KEY`，可以接 DeepSeek、Grok、OpenRouter 這類 OpenAI 相容服務。例如 `CUSTOM_BASE_URL=https://api.deepseek.com/v1`。
4. 到 **Deployments** 對最新一筆按 **Redeploy**，讓環境變數生效。
5. 打開 `https://你的專案.vercel.app`：
   1. 點「AI 設定」，輸入存取密碼，「後端網址」留空。
   2. 按「讀取可用模型」，每個模型欄位就能從清單挑。
   3. 幫每個人選好要用的 AI，按「儲存並連線」。

左上角顯示「多 AI 連線中」就完成了。

## 本機先試

```bash
pip install -r requirements.txt
export OFFICE_TOKEN=test123
export ANTHROPIC_API_KEY=...   # 有哪家設哪家
python api/index.py
```

打開 http://localhost:5000 ，在「AI 設定」輸入 `test123`。

直接雙擊 `public/index.html` 也能開，不過沒有後端，會跑示範模式，用預設內容走一遍流程。

## 前端想放 GitHub Pages？

把 `public/index.html` 放到 GitHub Pages，後端照樣部署在 Vercel。接著：

1. 在 Vercel 加一個環境變數 `ALLOWED_ORIGINS=https://你的帳號.github.io`。
2. 在網頁的「AI 設定 → 後端網址」填 `https://你的專案.vercel.app`。

## 注意事項

- **模型名稱**：預設的模型 ID 只是起點，各家常常改名或下架。如果出現「模型不存在」，按「讀取可用模型」換一個就好。
- **費用**：每次派工大約會呼叫 4 到 7 次 AI，主管規劃 1 次、每位員工各 1 次、審查 1 次、整合 1 次。費用各家分開算。
- **逾時**：`vercel.json` 把函式逾時設為 120 秒。用推理很久的大模型如果常逾時，就改用較快的模型。
- **研究類內容沒有上網**：Curry 是依模型本身的知識整理，需要查證的數字會標出來。
