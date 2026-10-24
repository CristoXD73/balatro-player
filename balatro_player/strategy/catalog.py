"""Game knowledge the scoring engine can't measure: economy, utility, tags."""

# Approximate dollars per round a joker earns.
ECONOMY_JOKERS = {
    "j_golden": 4, "j_rocket": 3, "j_cloud_9": 3, "j_to_the_moon": 2, "j_egg": 2,
    "j_delayed_grat": 2, "j_business": 2, "j_faceless": 1, "j_mail": 2, "j_trading": 2,
    "j_reserved_parking": 2, "j_satellite": 2, "j_gift": 1, "j_todo_list": 2, "j_rough_gem": 2,
    "j_matador": 1, "j_ticket": 1, "j_credit_card": 0.5,
}

# Jokers that change hand size (power is re-estimated with the new size).
HAND_SIZE_DELTA = {"j_juggler": 1, "j_turtle_bean": 4, "j_troubadour": 2, "j_stuntman": -2, "j_merry_andy": -1}

# Flat bonus (in "power ratio" units) for jokers whose value is mostly utility.
UTILITY_JOKERS = {
    "j_blueprint": 0.6, "j_brainstorm": 0.5, "j_drunkard": 0.1, "j_burglar": 0.05, "j_chaos": 0.1,
    "j_astronomer": 0.1, "j_ring_master": 0.05, "j_oops": 0.15, "j_mr_bones": 0.3, "j_chicot": 0.4,
    "j_luchador": 0.2, "j_diet_cola": 0.2, "j_invisible": 0.3, "j_perkeo": 0.3, "j_certificate": 0.15,
    "j_dna": 0.15, "j_marble": 0.05, "j_riff_raff": 0.15, "j_cartomancer": 0.15, "j_8_ball": 0.05,
    "j_hallucination": 0.05, "j_space": 0.2, "j_burnt": 0.25, "j_seance": 0.05, "j_sixth_sense": 0.05,
    "j_superposition": 0.05, "j_vagabond": 0.1, "j_midas_mask": 0.1, "j_hiker": 0.15,
}

# Scaling jokers start weak but grow; the engine sees today's value only.
SCALING_JOKERS = {
    "j_ride_the_bus": 0.3, "j_runner": 0.3, "j_green_joker": 0.2, "j_square": 0.2, "j_wee": 0.3,
    "j_hologram": 0.4, "j_constellation": 0.5, "j_lucky_cat": 0.4, "j_vampire": 0.3, "j_madness": 0.3,
    "j_obelisk": 0.3, "j_campfire": 0.3, "j_trousers": 0.3, "j_castle": 0.2, "j_glass": 0.2,
    "j_hit_the_road": 0.2, "j_caino": 0.3, "j_yorick": 0.3, "j_throwback": 0.2, "j_red_card": 0.15,
    "j_flash": 0.15, "j_fortune_teller": 0.15, "j_supernova": 0.25, "j_ceremonial": 0.2,
    "j_steel_joker": 0.1, "j_stone": 0.05, "j_erosion": 0.05,
}

GOOD_VOUCHERS = {
    "v_grabber": 3, "v_nacho_tong": 3, "v_wasteful": 2, "v_recyclomancy": 1, "v_paint_brush": 3,
    "v_palette": 2, "v_antimatter": 4, "v_overstock_norm": 2, "v_overstock_plus": 1,
    "v_clearance_sale": 2, "v_liquidation": 1, "v_seed_money": 2, "v_money_tree": 1,
    "v_reroll_surplus": 1, "v_crystal_ball": 1, "v_telescope": 1, "v_observatory": 2,
    "v_hieroglyph": 2, "v_petroglyph": 1, "v_directors_cut": 1, "v_retcon": 1, "v_hone": 1, "v_glow_up": 1,
    "v_planet_merchant": 1, "v_magic_trick": 0, "v_blank": 0,
}

# Tags worth skipping a blind for.
GOOD_TAGS = {
    "Negative Tag": 4, "Polychrome Tag": 3, "Rare Tag": 3, "Holographic Tag": 2, "Foil Tag": 1.5,
    "Uncommon Tag": 1.5, "Investment Tag": 2, "Double Tag": 1.5, "Voucher Tag": 1.5,
    "Economy Tag": 1.5, "Charm Tag": 1, "Meteor Tag": 1, "Buffoon Tag": 2, "Ethereal Tag": 0.5,
    "Coupon Tag": 1, "Juggle Tag": 0.5, "D6 Tag": 0.5, "Orbital Tag": 1.5, "Top-up Tag": 1,
    "Speed Tag": 1, "Garbage Tag": 0.5, "Handy Tag": 1, "Standard Tag": 0.3, "Boss Tag": 0.5,
}

PLANET_HAND = {
    "c_pluto": "High Card", "c_mercury": "Pair", "c_uranus": "Two Pair", "c_venus": "Three of a Kind",
    "c_saturn": "Straight", "c_jupiter": "Flush", "c_earth": "Full House", "c_mars": "Four of a Kind",
    "c_neptune": "Straight Flush", "c_planet_x": "Five of a Kind", "c_ceres": "Flush House",
    "c_eris": "Flush Five",
}

# Tarots: how many hand cards they target and what they do to them.
TAROT_ENHANCE = {
    "c_magician": ("LUCKY", 2), "c_empress": ("MULT", 2), "c_heirophant": ("BONUS", 2),
    "c_lovers": ("WILD", 1), "c_chariot": ("STEEL", 1), "c_justice": ("GLASS", 1),
    "c_devil": ("GOLD", 1), "c_tower": ("STONE", 1),
}
TAROT_SUIT = {"c_star": "D", "c_moon": "C", "c_sun": "H", "c_world": "S"}

# Spectral cards that are safe for a bot to use, and their target counts.
SAFE_SPECTRALS = {
    "c_black_hole": 0, "c_talisman": 1, "c_deja_vu": 1, "c_trance": 1, "c_medium": 1, "c_aura": 1,
    "c_cryptid": 1, "c_familiar": 0, "c_grim": 0, "c_incantation": 0, "c_immolate": 0,
    "c_soul": 0, "c_sigil": 0,
}
