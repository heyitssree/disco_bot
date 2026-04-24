# Deploying AstRobot to Google Cloud (Always Free Tier)

This guide walks you through setting up your Discord bot to run 24/7 on a Google Cloud Platform (GCP) **e2-micro** instance without incurring charges.

## Step 1: Create the Virtual Machine
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project or select an existing one. You may need to attach a credit card to enable billing, but you will not be charged as long as you stay within the Always Free limits.
3. Open the left menu, go to **Compute Engine > VM instances** and click **Create Instance**.
4. **Name:** `astrobot-server` (or whatever you prefer)
5. **Region (CRITICAL):** You MUST choose one of these three regions to get it for free:
   - `us-central1` (Iowa)
   - `us-east1` (South Carolina)
   - `us-west1` (Oregon)
6. **Machine Configuration:** 
   - Series: **E2**
   - Machine type: **e2-micro (2 vCPU, 1 GB memory)**
7. **Boot Disk:** Leave the defaults (Debian/Ubuntu 10GB is fine, free tier covers up to 30GB standard persistent disk).
8. Click **Create** at the bottom.

## Step 2: Access Your Server
1. Once the VM has booted up (showing a green checkmark), click the **SSH** button next to it in the Compute Engine dashboard.
2. A terminal window will pop up. You are now inside your remote Linux server!

## Step 3: Download Your Code & Setup Python
Inside the SSH terminal, run these commands carefully:

1. Update the server and install Python:
   ```bash
   sudo apt-get update
   sudo apt-get install python3 python3-pip python3-venv git tmux -y
   ```

2. Download your bot code:
   ```bash
   git clone https://github.com/heyitssree/disco_bot.git
   cd disco_bot
   ```
   *(If your repo is private, you may need to use a GitHub personal access token when cloning).*

3. Set up the Python virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. Install the bot's requirements:
   ```bash
   pip install -r requirements.txt
   ```

## Step 4: Add Your Tokens (.env file)
Your `.env` file (which holds your Discord and Gemini keys) is intentionally blocked by Git, so it didn't copy over during the clone. You must recreate it on the server:

1. Create and open the `.env` file:
   ```bash
   nano .env
   ```
2. Paste your keys inside using this format:
   ```env
   DISCORD_TOKEN=your-discord-token-here
   GEMINI_API_KEY=your-gemini-key-here
   ```
3. Press `Ctrl + O` then `Enter` to save it. Press `Ctrl + X` to exit Nano.

## Step 5: Keep the Bot Running Forever
If you just type `python bot.py` and close your SSH window, the bot will die. To keep it alive in the background, we use `tmux`.

1. Start a new background session:
   ```bash
   tmux new -s astrobot
   ```
2. Make sure you are in the folder and the virtual environment is active:
   ```bash
   cd ~/disco_bot
   source .venv/bin/activate
   ```
3. Start the bot!
   ```bash
   python bot.py
   ```
4. **Detaching:** Once the bot says "Logged in as AstRobot", press `Ctrl + B`, let go of both keys, and then press `D`. 
   
This "detaches" you from the tmux session while leaving the bot running. You can now close the browser tab. 

### How to check on it later
If you ever want to see your running bot logs or stop it:
1. Click the **SSH** button in Google Cloud again.
2. Type `tmux attach -t astrobot`.
3. To stop the bot, hit `Ctrl + C`.
