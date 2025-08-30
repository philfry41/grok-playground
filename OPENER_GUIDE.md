# Opener Guide

This guide describes the available opener files for starting stories with different character configurations and scenarios.

## **🎭 Available Openers**

### **2 Characters**
- **`opener_2char.txt`** - Emma & Unnamed Man at upscale restaurant
  - **Scenario**: Romantic dinner turns intimate
  - **Characters**: Emma (female), Unnamed Man (male)
  - **Setting**: Upscale restaurant with dim lighting
  - **Clothing**: Emma in silk blouse, Man in tailored suit

### **3 Characters**
- **`opener_3char.txt`** - Emma, Alex & Jordan in bedroom
  - **Scenario**: Threesome encounter
  - **Characters**: Emma (female), Alex (male), Jordan (female)
  - **Setting**: Dimly lit bedroom
  - **Clothing**: Emma in dress, Alex & Jordan fully dressed

### **4 Characters**
- **`opener_4char.txt`** - Rachel, Marcus, Sophia & David in penthouse
  - **Scenario**: Luxury foursome
  - **Characters**: Rachel (female), Marcus (male), Sophia (female), David (male)
  - **Setting**: Luxurious penthouse
  - **Clothing**: Rachel in cocktail dress, Marcus in suit, Sophia in designer dress, David in business attire

### **5 Characters**
- **`opener_5char.txt`** - Isabella, James, Elena, Carlos & Maya at exclusive club
  - **Scenario**: VIP club encounter
  - **Characters**: Isabella (female), James (male), Elena (female), Carlos (male), Maya (female)
  - **Setting**: Exclusive club VIP section
  - **Clothing**: All in designer/party attire

### **Office Scenarios**
- **`opener_office_3.txt`** - Jennifer, Mr. Thompson & Lisa in office
  - **Scenario**: Office threesome
  - **Characters**: Jennifer (female employee), Mr. Thompson (male boss), Lisa (female colleague)
  - **Setting**: Empty office after hours
  - **Clothing**: Professional business attire

### **Party Scenarios**
- **`opener_party_4.txt`** - Taylor, Chris, Ashley & Ryan at party
  - **Scenario**: College party encounter
  - **Characters**: Taylor (female), Chris (male), Ashley (female), Ryan (male)
  - **Setting**: Party bedroom
  - **Clothing**: Casual party attire

### **Swinger Scenarios**
- **`opener_swingers.txt`** - Michelle, Robert, Jessica & Michael in hotel
  - **Scenario**: Swinger party
  - **Characters**: Michelle (female), Robert (male), Jessica (female), Michael (male)
  - **Setting**: Luxury hotel suite
  - **Clothing**: Elegant evening wear

### **Bachelorette Party**
- **`opener_bachelorette.txt`** - Amanda, Brooke, Nicole, Vanessa, Tiffany & Destiny
  - **Scenario**: All-female bachelorette party
  - **Characters**: 6 women at bachelorette party
  - **Setting**: Luxury hotel suite
  - **Clothing**: Party dresses and cocktail attire

### **Fantasy RPG**
- **`opener_fantasy.txt`** - Aria, Thorne, Gimli, Pip & Zara in mystical chamber
  - **Scenario**: Fantasy RPG party encounter
  - **Characters**: Aria (elf), Thorne (orc), Gimli (dwarf), Pip (rogue), Zara (mage)
  - **Setting**: Mystical chamber
  - **Clothing**: Fantasy armor and robes

### **Specific Names**
- **`opener_sarah_mike.txt`** - Sarah & Mike in conference room
  - **Scenario**: Office romance
  - **Characters**: Sarah (female), Mike (male)
  - **Setting**: Empty conference room
  - **Clothing**: Professional business attire

## **🎯 Clothing Continuity Testing**

All openers have been revised to start with characters **fully dressed** to enable proper testing of the clothing continuity system. This allows the scene state manager to track clothing removal and prevent clothes from magically reappearing later in the story.

### **Testing Process:**
1. **Load opener** - Characters start fully dressed
2. **Continue story** - Clothing gets removed through natural progression
3. **Verify continuity** - Removed clothing stays removed
4. **Test persistence** - State maintained across multiple interactions

## **📝 Usage**

Use `/loadopener filename.txt` to start a story with the specified opener. The scene state manager will track character clothing, positions, and scene details to maintain continuity throughout the story.
