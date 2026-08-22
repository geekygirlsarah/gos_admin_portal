def get_contrast_color(hex_color):
    """
    Returns 'black' or 'white' based on the brightness of the input hex color.
    Expects hex_color in the format '#RRGGBB'.
    """
    if not hex_color or not hex_color.startswith("#") or len(hex_color) != 7:
        return "white"

    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)

        # Using the standard YIQ formula for perceived brightness
        # brightness = (R * 299 + G * 587 + B * 114) / 1000
        brightness = (r * 299 + g * 587 + b * 114) / 1000

        return "black" if brightness > 128 else "white"
    except (ValueError, IndexError):
        return "white"
