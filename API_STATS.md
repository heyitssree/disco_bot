# AstRobot V2 API Counters

The Gemini Service now tracks the number of times each key is used during the lifetime of the bot process.

### `/health` Command Output
The `Gemini API Usage` section of the health embed now shows:
- **Free**: Total successful calls using the free key (and percentage of total)
- **Paid**: Total successful calls using the paid key (and percentage of total)
- **Fails**: Total times *both* keys failed

This helps you see exactly how much money the free tier is saving you.
