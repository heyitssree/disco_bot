from typing import Optional

import discord

_CAT_NAMES: dict[int, str] = {
    9:  "General Knowledge",
    17: "Science & Nature",
    18: "Science: Computers",
    19: "Science: Mathematics",
    23: "History",
    22: "Geography",
    25: "Art",
    27: "Animals",
    21: "Sports",
    20: "Mythology",
    28: "Vehicles",
    30: "Science: Gadgets",
}


class GameConfigView(discord.ui.View):
    """Ephemeral config UI shown to the host before a game starts."""

    def __init__(self):
        super().__init__(timeout=30.0)
        self.total_rounds: int = 5
        self.question_difficulty: Optional[str] = None   # None = Mixed
        self.question_category: Optional[int] = None     # None = All
        self.confirmed: bool = False

        rounds_sel = discord.ui.Select(
            placeholder="Number of Rounds",
            options=[
                discord.SelectOption(label="3 Rounds", value="3"),
                discord.SelectOption(label="5 Rounds", value="5", default=True),
                discord.SelectOption(label="7 Rounds", value="7"),
                discord.SelectOption(label="10 Rounds", value="10"),
            ],
            row=0,
        )
        rounds_sel.callback = self._rounds_cb
        self.add_item(rounds_sel)

        diff_sel = discord.ui.Select(
            placeholder="Question Difficulty",
            options=[
                discord.SelectOption(label="Easy", value="easy"),
                discord.SelectOption(label="Medium", value="medium"),
                discord.SelectOption(label="Hard", value="hard"),
                discord.SelectOption(label="Mixed (random per question)", value="mixed", default=True),
            ],
            row=1,
        )
        diff_sel.callback = self._diff_cb
        self.add_item(diff_sel)

        cat_sel = discord.ui.Select(
            placeholder="Question Category",
            options=[
                discord.SelectOption(label="All Categories", value="all", default=True),
                discord.SelectOption(label="General Knowledge", value="9"),
                discord.SelectOption(label="Science & Nature", value="17"),
                discord.SelectOption(label="Science: Computers", value="18"),
                discord.SelectOption(label="Science: Mathematics", value="19"),
                discord.SelectOption(label="History", value="23"),
                discord.SelectOption(label="Geography", value="22"),
                discord.SelectOption(label="Art", value="25"),
                discord.SelectOption(label="Animals", value="27"),
                discord.SelectOption(label="Sports", value="21"),
                discord.SelectOption(label="Mythology", value="20"),
                discord.SelectOption(label="Vehicles", value="28"),
                discord.SelectOption(label="Science: Gadgets", value="30"),
            ],
            row=2,
        )
        cat_sel.callback = self._cat_cb
        self.add_item(cat_sel)

        confirm_btn = discord.ui.Button(
            label="✅ Confirm & Start",
            style=discord.ButtonStyle.success,
            row=3,
        )
        confirm_btn.callback = self._confirm_cb
        self.add_item(confirm_btn)

    async def _rounds_cb(self, interaction: discord.Interaction):
        self.total_rounds = int(interaction.data["values"][0])
        await interaction.response.defer()

    async def _diff_cb(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        self.question_difficulty = None if val == "mixed" else val
        await interaction.response.defer()

    async def _cat_cb(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        self.question_category = None if val == "all" else int(val)
        await interaction.response.defer()

    async def _confirm_cb(self, interaction: discord.Interaction):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    async def on_timeout(self):
        self.stop()

    def config_summary(self) -> str:
        diff_str = self.question_difficulty.title() if self.question_difficulty else "Mixed"
        cat_str = _CAT_NAMES.get(self.question_category, "All Categories") if self.question_category else "All Categories"
        return f"{self.total_rounds} rounds · {diff_str} · {cat_str}"
