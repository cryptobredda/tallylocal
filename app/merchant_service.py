"""
Merchant recognition service - maps transaction descriptions to known merchants with logos
"""

import re
from typing import Optional, Dict

# Top merchants with their logo URLs (using Clearbit Logo API for free logos)
# Format: 'keyword': {'name': 'Display Name', 'domain': 'domain.com'}
KNOWN_MERCHANTS = {
    # E-commerce & Retail
    'amazon': {'name': 'Amazon', 'domain': 'amazon.com'},
    'amzn': {'name': 'Amazon', 'domain': 'amazon.com'},
    'ebay': {'name': 'eBay', 'domain': 'ebay.com'},
    'etsy': {'name': 'Etsy', 'domain': 'etsy.com'},
    'walmart': {'name': 'Walmart', 'domain': 'walmart.com'},
    'target': {'name': 'Target', 'domain': 'target.com'},
    'costco': {'name': 'Costco', 'domain': 'costco.com'},
    'ikea': {'name': 'IKEA', 'domain': 'ikea.com'},
    'aliexpress': {'name': 'AliExpress', 'domain': 'aliexpress.com'},
    'wish': {'name': 'Wish', 'domain': 'wish.com'},
    'shein': {'name': 'SHEIN', 'domain': 'shein.com'},
    'asos': {'name': 'ASOS', 'domain': 'asos.com'},
    'zalando': {'name': 'Zalando', 'domain': 'zalando.com'},
    'hm': {'name': 'H&M', 'domain': 'hm.com'},
    'zara': {'name': 'Zara', 'domain': 'zara.com'},
    'uniqlo': {'name': 'Uniqlo', 'domain': 'uniqlo.com'},
    'primark': {'name': 'Primark', 'domain': 'primark.com'},
    'next': {'name': 'Next', 'domain': 'next.co.uk'},
    'argos': {'name': 'Argos', 'domain': 'argos.co.uk'},
    'currys': {'name': 'Currys', 'domain': 'currys.co.uk'},
    'john lewis': {'name': 'John Lewis', 'domain': 'johnlewis.com'},
    'marks spencer': {'name': 'M&S', 'domain': 'marksandspencer.com'},
    'm&s': {'name': 'M&S', 'domain': 'marksandspencer.com'},
    'boots': {'name': 'Boots', 'domain': 'boots.com'},
    'superdrug': {'name': 'Superdrug', 'domain': 'superdrug.com'},
    
    # Streaming & Entertainment
    'netflix': {'name': 'Netflix', 'domain': 'netflix.com'},
    'spotify': {'name': 'Spotify', 'domain': 'spotify.com'},
    'apple music': {'name': 'Apple Music', 'domain': 'apple.com'},
    'disney': {'name': 'Disney+', 'domain': 'disneyplus.com'},
    'hulu': {'name': 'Hulu', 'domain': 'hulu.com'},
    'hbo': {'name': 'HBO Max', 'domain': 'hbomax.com'},
    'prime video': {'name': 'Prime Video', 'domain': 'primevideo.com'},
    'youtube': {'name': 'YouTube', 'domain': 'youtube.com'},
    'twitch': {'name': 'Twitch', 'domain': 'twitch.tv'},
    'audible': {'name': 'Audible', 'domain': 'audible.com'},
    'kindle': {'name': 'Kindle', 'domain': 'amazon.com'},
    'crunchyroll': {'name': 'Crunchyroll', 'domain': 'crunchyroll.com'},
    'paramount': {'name': 'Paramount+', 'domain': 'paramountplus.com'},
    'now tv': {'name': 'NOW TV', 'domain': 'nowtv.com'},
    'sky': {'name': 'Sky', 'domain': 'sky.com'},
    
    # Food & Delivery
    'uber eats': {'name': 'Uber Eats', 'domain': 'ubereats.com'},
    'ubereats': {'name': 'Uber Eats', 'domain': 'ubereats.com'},
    'deliveroo': {'name': 'Deliveroo', 'domain': 'deliveroo.com'},
    'doordash': {'name': 'DoorDash', 'domain': 'doordash.com'},
    'just eat': {'name': 'Just Eat', 'domain': 'just-eat.com'},
    'justeat': {'name': 'Just Eat', 'domain': 'just-eat.com'},
    'grubhub': {'name': 'Grubhub', 'domain': 'grubhub.com'},
    'postmates': {'name': 'Postmates', 'domain': 'postmates.com'},
    'instacart': {'name': 'Instacart', 'domain': 'instacart.com'},
    'hellofresh': {'name': 'HelloFresh', 'domain': 'hellofresh.com'},
    'gousto': {'name': 'Gousto', 'domain': 'gousto.co.uk'},
    
    # Coffee & Fast Food
    'starbucks': {'name': 'Starbucks', 'domain': 'starbucks.com'},
    'costa': {'name': 'Costa Coffee', 'domain': 'costa.co.uk'},
    'pret': {'name': 'Pret A Manger', 'domain': 'pret.com'},
    'mcdonalds': {'name': "McDonald's", 'domain': 'mcdonalds.com'},
    'mcdonald': {'name': "McDonald's", 'domain': 'mcdonalds.com'},
    'burger king': {'name': 'Burger King', 'domain': 'burgerking.com'},
    'kfc': {'name': 'KFC', 'domain': 'kfc.com'},
    'subway': {'name': 'Subway', 'domain': 'subway.com'},
    'dominos': {'name': "Domino's", 'domain': 'dominos.com'},
    'pizza hut': {'name': 'Pizza Hut', 'domain': 'pizzahut.com'},
    'papa john': {'name': "Papa John's", 'domain': 'papajohns.com'},
    'nandos': {'name': "Nando's", 'domain': 'nandos.com'},
    'greggs': {'name': 'Greggs', 'domain': 'greggs.co.uk'},
    'five guys': {'name': 'Five Guys', 'domain': 'fiveguys.com'},
    'chipotle': {'name': 'Chipotle', 'domain': 'chipotle.com'},
    'taco bell': {'name': 'Taco Bell', 'domain': 'tacobell.com'},
    'wendy': {'name': "Wendy's", 'domain': 'wendys.com'},
    'chick-fil-a': {'name': 'Chick-fil-A', 'domain': 'chick-fil-a.com'},
    'dunkin': {'name': "Dunkin'", 'domain': 'dunkindonuts.com'},
    'tim horton': {'name': 'Tim Hortons', 'domain': 'timhortons.com'},
    'cafe nero': {'name': 'Caffè Nero', 'domain': 'caffenero.com'},
    
    # Supermarkets & Grocery
    'tesco': {'name': 'Tesco', 'domain': 'tesco.com'},
    'sainsbury': {'name': "Sainsbury's", 'domain': 'sainsburys.co.uk'},
    'asda': {'name': 'ASDA', 'domain': 'asda.com'},
    'morrisons': {'name': 'Morrisons', 'domain': 'morrisons.com'},
    'aldi': {'name': 'Aldi', 'domain': 'aldi.co.uk'},
    'lidl': {'name': 'Lidl', 'domain': 'lidl.co.uk'},
    'waitrose': {'name': 'Waitrose', 'domain': 'waitrose.com'},
    'co-op': {'name': 'Co-op', 'domain': 'coop.co.uk'},
    'ocado': {'name': 'Ocado', 'domain': 'ocado.com'},
    'whole foods': {'name': 'Whole Foods', 'domain': 'wholefoodsmarket.com'},
    'trader joe': {'name': "Trader Joe's", 'domain': 'traderjoes.com'},
    'kroger': {'name': 'Kroger', 'domain': 'kroger.com'},
    'publix': {'name': 'Publix', 'domain': 'publix.com'},
    'iceland': {'name': 'Iceland', 'domain': 'iceland.co.uk'},
    
    # Transport & Travel
    'uber': {'name': 'Uber', 'domain': 'uber.com'},
    'lyft': {'name': 'Lyft', 'domain': 'lyft.com'},
    'bolt': {'name': 'Bolt', 'domain': 'bolt.eu'},
    'trainline': {'name': 'Trainline', 'domain': 'thetrainline.com'},
    'trainpal': {'name': 'TrainPal', 'domain': 'trainpal.com'},
    'national rail': {'name': 'National Rail', 'domain': 'nationalrail.co.uk'},
    'tfl': {'name': 'TfL', 'domain': 'tfl.gov.uk'},
    'oyster': {'name': 'TfL Oyster', 'domain': 'tfl.gov.uk'},
    'citymapper': {'name': 'Citymapper', 'domain': 'citymapper.com'},
    'airbnb': {'name': 'Airbnb', 'domain': 'airbnb.com'},
    'booking.com': {'name': 'Booking.com', 'domain': 'booking.com'},
    'expedia': {'name': 'Expedia', 'domain': 'expedia.com'},
    'hotels.com': {'name': 'Hotels.com', 'domain': 'hotels.com'},
    'skyscanner': {'name': 'Skyscanner', 'domain': 'skyscanner.com'},
    'kayak': {'name': 'Kayak', 'domain': 'kayak.com'},
    'ryanair': {'name': 'Ryanair', 'domain': 'ryanair.com'},
    'easyjet': {'name': 'easyJet', 'domain': 'easyjet.com'},
    'british airways': {'name': 'British Airways', 'domain': 'britishairways.com'},
    'virgin atlantic': {'name': 'Virgin Atlantic', 'domain': 'virginatlantic.com'},
    'delta': {'name': 'Delta', 'domain': 'delta.com'},
    'united': {'name': 'United Airlines', 'domain': 'united.com'},
    'american airlines': {'name': 'American Airlines', 'domain': 'aa.com'},
    'southwest': {'name': 'Southwest', 'domain': 'southwest.com'},
    'jetblue': {'name': 'JetBlue', 'domain': 'jetblue.com'},
    'spirit': {'name': 'Spirit Airlines', 'domain': 'spirit.com'},
    'emirates': {'name': 'Emirates', 'domain': 'emirates.com'},
    'qatar': {'name': 'Qatar Airways', 'domain': 'qatarairways.com'},
    'lufthansa': {'name': 'Lufthansa', 'domain': 'lufthansa.com'},
    'hertz': {'name': 'Hertz', 'domain': 'hertz.com'},
    'enterprise': {'name': 'Enterprise', 'domain': 'enterprise.com'},
    'avis': {'name': 'Avis', 'domain': 'avis.com'},
    'sixt': {'name': 'Sixt', 'domain': 'sixt.com'},
    'eurocar': {'name': 'Europcar', 'domain': 'europcar.com'},
    
    # Tech & Software
    'apple': {'name': 'Apple', 'domain': 'apple.com'},
    'google': {'name': 'Google', 'domain': 'google.com'},
    'microsoft': {'name': 'Microsoft', 'domain': 'microsoft.com'},
    'adobe': {'name': 'Adobe', 'domain': 'adobe.com'},
    'dropbox': {'name': 'Dropbox', 'domain': 'dropbox.com'},
    'github': {'name': 'GitHub', 'domain': 'github.com'},
    'slack': {'name': 'Slack', 'domain': 'slack.com'},
    'zoom': {'name': 'Zoom', 'domain': 'zoom.us'},
    'notion': {'name': 'Notion', 'domain': 'notion.so'},
    'figma': {'name': 'Figma', 'domain': 'figma.com'},
    'canva': {'name': 'Canva', 'domain': 'canva.com'},
    'openai': {'name': 'OpenAI', 'domain': 'openai.com'},
    'chatgpt': {'name': 'OpenAI', 'domain': 'openai.com'},
    
    # Payment & Financial
    'paypal': {'name': 'PayPal', 'domain': 'paypal.com'},
    'stripe': {'name': 'Stripe', 'domain': 'stripe.com'},
    'square': {'name': 'Square', 'domain': 'squareup.com'},
    'venmo': {'name': 'Venmo', 'domain': 'venmo.com'},
    'revolut': {'name': 'Revolut', 'domain': 'revolut.com'},
    'monzo': {'name': 'Monzo', 'domain': 'monzo.com'},
    'starling': {'name': 'Starling', 'domain': 'starlingbank.com'},
    'wise': {'name': 'Wise', 'domain': 'wise.com'},
    'transferwise': {'name': 'Wise', 'domain': 'wise.com'},
    'coinbase': {'name': 'Coinbase', 'domain': 'coinbase.com'},
    'binance': {'name': 'Binance', 'domain': 'binance.com'},
    'klarna': {'name': 'Klarna', 'domain': 'klarna.com'},
    'clearpay': {'name': 'Clearpay', 'domain': 'clearpay.com'},
    'afterpay': {'name': 'Afterpay', 'domain': 'afterpay.com'},
    
    # Gaming
    'steam': {'name': 'Steam', 'domain': 'steampowered.com'},
    'playstation': {'name': 'PlayStation', 'domain': 'playstation.com'},
    'xbox': {'name': 'Xbox', 'domain': 'xbox.com'},
    'nintendo': {'name': 'Nintendo', 'domain': 'nintendo.com'},
    'epic games': {'name': 'Epic Games', 'domain': 'epicgames.com'},
    'roblox': {'name': 'Roblox', 'domain': 'roblox.com'},
    'ea': {'name': 'EA', 'domain': 'ea.com'},
    'blizzard': {'name': 'Blizzard', 'domain': 'blizzard.com'},
    
    # Fitness & Health
    'peloton': {'name': 'Peloton', 'domain': 'onepeloton.com'},
    'gymshark': {'name': 'Gymshark', 'domain': 'gymshark.com'},
    'nike': {'name': 'Nike', 'domain': 'nike.com'},
    'adidas': {'name': 'Adidas', 'domain': 'adidas.com'},
    'puma': {'name': 'Puma', 'domain': 'puma.com'},
    'under armour': {'name': 'Under Armour', 'domain': 'underarmour.com'},
    'lululemon': {'name': 'Lululemon', 'domain': 'lululemon.com'},
    'myprotein': {'name': 'Myprotein', 'domain': 'myprotein.com'},
    'headspace': {'name': 'Headspace', 'domain': 'headspace.com'},
    'calm': {'name': 'Calm', 'domain': 'calm.com'},
    'strava': {'name': 'Strava', 'domain': 'strava.com'},
    'fitbit': {'name': 'Fitbit', 'domain': 'fitbit.com'},
    'puregym': {'name': 'PureGym', 'domain': 'puregym.com'},
    'pure gym': {'name': 'PureGym', 'domain': 'puregym.com'},
    'planet fitness': {'name': 'Planet Fitness', 'domain': 'planetfitness.com'},
    
    # Utilities & Services
    'british gas': {'name': 'British Gas', 'domain': 'britishgas.co.uk'},
    'edf': {'name': 'EDF Energy', 'domain': 'edfenergy.com'},
    'octopus': {'name': 'Octopus Energy', 'domain': 'octopus.energy'},
    'ovo': {'name': 'OVO Energy', 'domain': 'ovoenergy.com'},
    'bt': {'name': 'BT', 'domain': 'bt.com'},
    'virgin media': {'name': 'Virgin Media', 'domain': 'virginmedia.com'},
    'vodafone': {'name': 'Vodafone', 'domain': 'vodafone.co.uk'},
    'ee': {'name': 'EE', 'domain': 'ee.co.uk'},
    'three': {'name': 'Three', 'domain': 'three.co.uk'},
    'o2': {'name': 'O2', 'domain': 'o2.co.uk'},
    'giffgaff': {'name': 'giffgaff', 'domain': 'giffgaff.com'},
    'verizon': {'name': 'Verizon', 'domain': 'verizon.com'},
    't-mobile': {'name': 'T-Mobile', 'domain': 't-mobile.com'},
    'at&t': {'name': 'AT&T', 'domain': 'att.com'},
    'comcast': {'name': 'Comcast', 'domain': 'comcast.com'},
    'xfinity': {'name': 'Xfinity', 'domain': 'xfinity.com'},
    'spectrum': {'name': 'Spectrum', 'domain': 'spectrum.com'},
    
    # Insurance
    'geico': {'name': 'GEICO', 'domain': 'geico.com'},
    'progressive': {'name': 'Progressive', 'domain': 'progressive.com'},
    'state farm': {'name': 'State Farm', 'domain': 'statefarm.com'},
    'allstate': {'name': 'Allstate', 'domain': 'allstate.com'},
    'aviva': {'name': 'Aviva', 'domain': 'aviva.co.uk'},
    'admiral': {'name': 'Admiral', 'domain': 'admiral.com'},
    'direct line': {'name': 'Direct Line', 'domain': 'directline.com'},
    
    # Other popular
    'linkedin': {'name': 'LinkedIn', 'domain': 'linkedin.com'},
    'twitter': {'name': 'Twitter/X', 'domain': 'x.com'},
    'facebook': {'name': 'Facebook', 'domain': 'facebook.com'},
    'meta': {'name': 'Meta', 'domain': 'meta.com'},
    'instagram': {'name': 'Instagram', 'domain': 'instagram.com'},
    'tiktok': {'name': 'TikTok', 'domain': 'tiktok.com'},
    'snapchat': {'name': 'Snapchat', 'domain': 'snapchat.com'},
    'pinterest': {'name': 'Pinterest', 'domain': 'pinterest.com'},
    'reddit': {'name': 'Reddit', 'domain': 'reddit.com'},
    'discord': {'name': 'Discord', 'domain': 'discord.com'},
    'whatsapp': {'name': 'WhatsApp', 'domain': 'whatsapp.com'},
    'telegram': {'name': 'Telegram', 'domain': 'telegram.org'},
}

def get_logo_url(domain: str, size: int = 64) -> str:
    """Get logo URL from Clearbit Logo API (free, no API key required)"""
    return f"https://logo.clearbit.com/{domain}?size={size}"

def recognize_merchant(description: str) -> Optional[Dict]:
    """
    Attempt to recognize a merchant from transaction description.
    Returns merchant info with logo URL if recognized, None otherwise.
    """
    if not description:
        return None
    
    desc_lower = description.lower()
    
    # Try to match against known merchants
    for keyword, merchant_info in KNOWN_MERCHANTS.items():
        # Use word boundary matching to avoid false positives
        # e.g., "amazon" should match "AMAZON.CO.UK" but not "AMAZONIAFOODS"
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, desc_lower):
            return {
                'name': merchant_info['name'],
                'domain': merchant_info['domain'],
                'logo_url': get_logo_url(merchant_info['domain']),
                'matched_keyword': keyword
            }
    
    return None

def get_merchant_logo(description: str, size: int = 32) -> Optional[str]:
    """
    Get just the logo URL for a transaction description.
    Returns None if merchant not recognized.
    """
    merchant = recognize_merchant(description)
    if merchant:
        return get_logo_url(merchant['domain'], size)
    return None

def enrich_transaction(transaction_dict: Dict) -> Dict:
    """
    Enrich a transaction dictionary with merchant info if recognized.
    """
    description = transaction_dict.get('description', '')
    merchant = recognize_merchant(description)
    
    if merchant:
        transaction_dict['merchant'] = merchant
    
    return transaction_dict

def enrich_transactions(transactions: list) -> list:
    """
    Enrich a list of transaction dictionaries with merchant info.
    """
    return [enrich_transaction(t) for t in transactions]
