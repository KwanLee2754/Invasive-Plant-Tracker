from flask import Flask, render_template, jsonify
from flask_cors import CORS
import requests
from datetime import datetime, timedelta
import logging
import json
import time




app = Flask(__name__)
CORS(app)




# Configure detailed logging
logging.basicConfig(
  level=logging.INFO,
  format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)




# Target invasive plants taxon IDs (names will be fetched from API)
TARGET_TAXON_IDS = [56061, 77835, 204237, 82342, 55882, 64540, 78882, 58727, 131249, 64697, 130893, 62671]




# Cache for taxon info from API
TAXON_INFO_CACHE = {}




# iNaturalist API endpoint
INATURALIST_API_URL = "https://api.inaturalist.org/v1/observations"
INATURALIST_TAXA_API_URL = "https://api.inaturalist.org/v1/taxa"








def fetch_taxon_info(taxon_id):
  """
  Fetch taxon information from iNaturalist API to get the actual common name
  """
  if taxon_id in TAXON_INFO_CACHE:
      return TAXON_INFO_CACHE[taxon_id]




  try:
      response = requests.get(f"{INATURALIST_TAXA_API_URL}/{taxon_id}", timeout=10)
      if response.status_code == 200:
          data = response.json()
          if 'results' in data and len(data['results']) > 0:
              taxon = data['results'][0]
              common_name = taxon.get('preferred_common_name', f'Species {taxon_id}')
              scientific_name = taxon.get('name', 'Unknown')




              TAXON_INFO_CACHE[taxon_id] = {
                  'common_name': common_name,
                  'scientific_name': scientific_name
              }
              logger.info(f"Fetched taxon info for {taxon_id}: {common_name} ({scientific_name})")
              return TAXON_INFO_CACHE[taxon_id]
  except Exception as e:
      logger.error(f"Error fetching taxon info for {taxon_id}: {e}")




  # Fallback
  TAXON_INFO_CACHE[taxon_id] = {
      'common_name': f'Species {taxon_id}',
      'scientific_name': 'Unknown'
  }
  return TAXON_INFO_CACHE[taxon_id]








def initialize_taxon_cache():
  """
  Pre-fetch all taxon information at startup
  """
  logger.info("Initializing taxon information cache...")
  for taxon_id in TARGET_TAXON_IDS:
      fetch_taxon_info(taxon_id)
  logger.info("Taxon cache initialized")








def normalize_text(text):
  """Normalize text for case-insensitive comparison"""
  if text is None:
      return ""
  return str(text).lower().strip()








def check_condition_1_target_species(obs_data):
  """
  Condition 1: Must be one of the target invasive species
  Check if taxon_id matches any of the target IDs
  """
  if 'taxon' in obs_data and obs_data['taxon']:
      taxon = obs_data['taxon']
      taxon_id = taxon.get('id')




      # Check if it's one of our target species
      if taxon_id in TARGET_TAXON_IDS:
          taxon_info = TAXON_INFO_CACHE.get(taxon_id, {})
          species_name = taxon_info.get('common_name', f'Species {taxon_id}')
          logger.debug(f"✓ Condition 1: Confirmed {species_name} by taxon ID {taxon_id}")
          return True, taxon_id




      # Log non-matching species for debugging
      if taxon_id:
          common_name = taxon.get('preferred_common_name', 'Unknown')
          logger.debug(f"✗ Not target species - ID: {taxon_id}, Name: {common_name}")




  return False, None








def check_condition_2_maryland(obs_data):
  """
  Condition 2: Must be in Maryland
  """
  place_guess = normalize_text(obs_data.get('place_guess', ''))




  # Check for Maryland or MD
  maryland_found = any([
      'maryland' in place_guess,
      ', md' in place_guess,
      ' md ' in place_guess,
      ', md,' in place_guess,
      place_guess.endswith(', md'),
      place_guess.endswith(' md')
  ])




  if maryland_found:
      logger.debug(f"✓ Condition 2: Maryland found in place_guess")
      return True




  # Check place_ids (Maryland = 52)
  if 'place_ids' in obs_data:
      place_ids = obs_data.get('place_ids', [])
      if isinstance(place_ids, list) and 52 in place_ids:
          logger.debug(f"✓ Condition 2: Maryland found by place_id 52")
          return True




  return False








def check_condition_3_montgomery(obs_data):
  """
  Condition 3: Must be in Montgomery County
  """
  place_guess = normalize_text(obs_data.get('place_guess', ''))




  # Check for Montgomery
  if 'montgomery' in place_guess:
      logger.debug(f"✓ Condition 3: Montgomery found in place_guess")
      return True




  # Check by coordinates (Montgomery County bounds)
  lat = None
  lon = None




  if 'geojson' in obs_data and obs_data['geojson']:
      coords = obs_data['geojson'].get('coordinates', [])
      if len(coords) >= 2:
          lon, lat = coords[0], coords[1]
  elif 'location' in obs_data and obs_data['location']:
      try:
          parts = obs_data['location'].split(',')
          if len(parts) == 2:
              lat = float(parts[0].strip())
              lon = float(parts[1].strip())
      except:
          pass




  # Montgomery County approximate bounds
  if lat and lon:
      if 38.98 <= lat <= 39.35 and -77.54 <= lon <= -76.89:
          logger.debug(f"✓ Condition 3: Within Montgomery County bounds: {lat}, {lon}")
          return True




  return False








def check_condition_4_date_range(obs_data):
  """
  Condition 4: Observation date must be within last 6 months (exactly 6 months ago to today)
  Example: If today is Sept 30, 2025, then from March 30, 2025 to Sept 30, 2025
  """
  observed_on = obs_data.get('observed_on') or obs_data.get('observed_on_string')




  if not observed_on:
      return False




  try:
      if 'T' in str(observed_on):
          date_str = observed_on.split('T')[0]
      else:
          date_str = str(observed_on)




      obs_date = datetime.strptime(date_str, '%Y-%m-%d')




      # Calculate exactly 6 months ago from today
      today = datetime.now()




      # Calculate 6 months ago - same day, 6 months earlier
      target_month = today.month - 6
      target_year = today.year
      target_day = today.day




      if target_month <= 0:
          target_month += 12
          target_year -= 1




      # Handle day overflow (e.g., Jan 31 -> 6 months back could be July 31)
      try:
          cutoff_date = datetime(target_year, target_month, target_day)
      except ValueError:
          # If the day doesn't exist in that month (e.g., Feb 30), use last day of that month
          import calendar
          last_day = calendar.monthrange(target_year, target_month)[1]
          cutoff_date = datetime(target_year, target_month, last_day)




      if cutoff_date <= obs_date <= today:
          return True




  except Exception as e:
      logger.debug(f"Date parsing error: {e}")




  return False








def check_all_conditions(obs_data):
  """
  Check if observation meets ALL FOUR conditions
  """
  obs_id = obs_data.get('id', 'Unknown')




  # Check condition 1 (target species)
  is_target_species, taxon_id = check_condition_1_target_species(obs_data)
  if not is_target_species:
      return False




  # Check other conditions
  cond2 = check_condition_2_maryland(obs_data)
  cond3 = check_condition_3_montgomery(obs_data)
  cond4 = check_condition_4_date_range(obs_data)




  if cond2 and cond3 and cond4:
      taxon_info = TAXON_INFO_CACHE.get(taxon_id, {})
      species_name = taxon_info.get('common_name', f'Species {taxon_id}')
      logger.debug(f"✅ Observation #{obs_id}: {species_name} passes all conditions")
      return True




  return False








def extract_observation_data(obs_data):
  """
  Extract required fields from observation
  """
  result = {
      'id': obs_data.get('id'),
      'taxon_id': None,
      'common_name': 'Unknown',
      'scientific_name': 'Unknown',
      'image_url': None,
      'longitude': None,
      'latitude': None,
      'observed_on': None,
      'place_state_name': 'Maryland',
      'place_county_name': 'Montgomery',
      'place_guess': obs_data.get('place_guess', ''),
      'observer': 'Unknown',
      'quality_grade': obs_data.get('quality_grade', 'unknown'),
      'url': f"https://www.inaturalist.org/observations/{obs_data.get('id')}"
  }




  # Extract taxon information - use API common name
  if 'taxon' in obs_data and obs_data['taxon']:
      taxon = obs_data['taxon']
      result['taxon_id'] = taxon.get('id')
      # Use the common name from the observation response
      result['common_name'] = taxon.get('preferred_common_name', 'Unknown')
      result['scientific_name'] = taxon.get('name', 'Unknown')




  # Extract coordinates
  if 'geojson' in obs_data and obs_data['geojson']:
      coords = obs_data['geojson'].get('coordinates', [])
      if len(coords) >= 2:
          result['longitude'] = coords[0]
          result['latitude'] = coords[1]
  elif 'location' in obs_data and obs_data['location']:
      try:
          parts = obs_data['location'].split(',')
          if len(parts) == 2:
              result['latitude'] = float(parts[0].strip())
              result['longitude'] = float(parts[1].strip())
      except:
          pass




  # Extract image URL
  if 'photos' in obs_data and obs_data['photos']:
      photos = obs_data['photos']
      if len(photos) > 0 and isinstance(photos[0], dict):
          photo_url = photos[0].get('url', '')
          if photo_url:
              result['image_url'] = photo_url.replace('square', 'medium')




  # Extract date
  observed_on = obs_data.get('observed_on') or obs_data.get('observed_on_string', '')
  if observed_on and 'T' in str(observed_on):
      observed_on = observed_on.split('T')[0]
  result['observed_on'] = observed_on




  # Extract observer
  if 'user' in obs_data and obs_data['user']:
      result['observer'] = obs_data['user'].get('login', 'Unknown')




  return result








def fetch_all_target_species():
  """
  Fetch observations for ALL 10 target species
  Uses proper OR logic to get all species
  """
  all_observations = []
  processed_ids = set()




  # Date range: Last 6 months (exactly 6 months ago to today)
  today = datetime.now()




  # Calculate exactly 6 months ago
  target_month = today.month - 6
  target_year = today.year
  target_day = today.day




  if target_month <= 0:
      target_month += 12
      target_year -= 1




  # Handle day overflow
  try:
      start_date = datetime(target_year, target_month, target_day)
  except ValueError:
      import calendar
      last_day = calendar.monthrange(target_year, target_month)[1]
      start_date = datetime(target_year, target_month, last_day)




  end_date = today




  logger.info(f"\n{'=' * 80}")
  logger.info(f"FETCHING ALL TARGET SPECIES")
  logger.info(f"Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
  logger.info(f"Target taxon IDs: {TARGET_TAXON_IDS}")
  logger.info(f"{'=' * 80}")




  # CRITICAL: Search for EACH species separately to ensure we get all
  for taxon_id in TARGET_TAXON_IDS:
      taxon_info = TAXON_INFO_CACHE.get(taxon_id, {})
      species_name = taxon_info.get('common_name', f'Species {taxon_id}')




      logger.info(f"\n{'─' * 60}")
      logger.info(f"Searching for {species_name} (Taxon ID: {taxon_id})")
      logger.info(f"{'─' * 60}")




      species_count = 0




      # Try multiple search approaches for each species
      search_params_list = [
          # Approach 1: Direct taxon ID search in Maryland
          {
              'taxon_id': str(taxon_id),
              'place_id': 52,  # Maryland
              'd1': start_date.strftime('%Y-%m-%d'),
              'd2': end_date.strftime('%Y-%m-%d')
          },
          # Approach 2: Taxon ID with Montgomery County bounding box
          {
              'taxon_id': str(taxon_id),
              'nelat': 39.35,
              'nelng': -76.89,
              'swlat': 38.98,
              'swlng': -77.54,
              'd1': start_date.strftime('%Y-%m-%d'),
              'd2': end_date.strftime('%Y-%m-%d')
          },
          # Approach 3: Taxon ID with text search
          {
              'taxon_id': str(taxon_id),
              'q': 'Montgomery',
              'd1': start_date.strftime('%Y-%m-%d'),
              'd2': end_date.strftime('%Y-%m-%d')
          }
      ]




      for approach_num, base_params in enumerate(search_params_list, 1):
          logger.info(f"  Approach {approach_num}/3...")




          params = {
              **base_params,
              'order': 'desc',
              'order_by': 'observed_on',
              'has': 'geo',
              'geo': True,
              'per_page': 200,
              'verifiable': True
          }




          # Fetch up to 2 pages per approach
          for page in range(1, 3):
              params['page'] = page




              try:
                  response = requests.get(INATURALIST_API_URL, params=params, timeout=30)




                  if response.status_code != 200:
                      logger.warning(f"    API returned status {response.status_code}")
                      break




                  data = response.json()
                  results = data.get('results', [])




                  if not results:
                      break




                  logger.info(f"    Page {page}: Found {len(results)} observations")




                  # Process each observation
                  for obs in results:
                      obs_id = obs.get('id')




                      if obs_id in processed_ids:
                          continue




                      # Verify it's our target species AND meets location/date criteria
                      if check_all_conditions(obs):
                          obs_data = extract_observation_data(obs)




                          if obs_data['longitude'] and obs_data['latitude']:
                              all_observations.append(obs_data)
                              processed_ids.add(obs_id)
                              species_count += 1




                              logger.info(f"      ✅ Added: {obs_data['common_name']} - {obs_data['observed_on']}")




                  if len(results) < 200:
                      break




                  time.sleep(0.3)  # Rate limiting




              except Exception as e:
                  logger.error(f"    Error: {e}")
                  continue




      logger.info(f"  Total {species_name} observations: {species_count}")




  # Sort by date (newest first)
  all_observations.sort(key=lambda x: x.get('observed_on', ''), reverse=True)




  logger.info(f"\n{'=' * 80}")
  logger.info(f"✅ FINAL RESULTS")
  logger.info(f"Total observations: {len(all_observations)}")
  logger.info(f"{'=' * 80}\n")




  return all_observations








@app.route('/')
def index():
  """Serve the main page"""
  return render_template('index.html')








@app.route('/api/observations')
def get_observations():
  """API endpoint to fetch filtered observations"""
  try:
      start_time = time.time()




      observations = fetch_all_target_species()




      # Build species_info from actual common names in observations
      # IMPORTANT: Include ALL target species, even if they have 0 observations
      species_info = {}
      species_counts = {}




      # First, initialize all target species with their names from cache
      for taxon_id in TARGET_TAXON_IDS:
          taxon_info = TAXON_INFO_CACHE.get(taxon_id, {})
          common_name = taxon_info.get('common_name', f'Species {taxon_id}')
          species_info[taxon_id] = common_name
          species_counts[common_name] = 0




      # Then, count observations for each species (only those with coordinates)
      for obs in observations:
          taxon_id = obs.get('taxon_id')
          common_name = obs.get('common_name', 'Unknown')




          if taxon_id and taxon_id in TARGET_TAXON_IDS:
              # Update the common_name from actual observation if available
              if taxon_id in species_info:
                  species_info[taxon_id] = common_name




              # Count occurrences (only observations with coordinates)
              if common_name in species_counts:
                  species_counts[common_name] += 1
              else:
                  species_counts[common_name] = 1




      # Calculate statistics
      stats = {
          'total': len(observations),
          'by_species': species_counts
      }




      logger.info(f"API response - Total: {stats['total']}, By species: {stats['by_species']}")




      return jsonify({
          'success': True,
          'data': observations,
          'stats': stats,
          'species_info': species_info,  # {taxon_id: common_name} includes ALL target species
          'processing_time': round(time.time() - start_time, 2),
          'timestamp': datetime.now().isoformat()
      })




  except Exception as e:
      logger.error(f"Error in get_observations: {str(e)}", exc_info=True)
      return jsonify({
          'success': False,
          'error': str(e),
          'data': [],
          'stats': {},
          'species_info': {}
      }), 500








@app.route('/api/health')
def health_check():
  """Health check endpoint"""
  species_names = [TAXON_INFO_CACHE.get(tid, {}).get('common_name', f'Species {tid}')
                   for tid in TARGET_TAXON_IDS]
  return jsonify({
      'status': 'healthy',
      'service': 'Invasive Plants Tracking API',
      'version': '3.2.0',
      'target_species': species_names,
      'target_species_count': len(TARGET_TAXON_IDS),
      'timestamp': datetime.now().isoformat()
  })








if __name__ == '__main__':
  print("\n" + "=" * 80)
  print(" INVASIVE PLANTS OBSERVATION TRACKING SYSTEM v3.2")
  print("=" * 80)
  print(" Initializing taxon information from iNaturalist API...")
  initialize_taxon_cache()
  print(f" Target Species ({len(TARGET_TAXON_IDS)} invasive plants):")
  for taxon_id in TARGET_TAXON_IDS:
      taxon_info = TAXON_INFO_CACHE.get(taxon_id, {})
      common_name = taxon_info.get('common_name', 'Unknown')
      scientific_name = taxon_info.get('scientific_name', 'Unknown')
      print(f"   • {common_name} - {scientific_name} (Taxon ID: {taxon_id})")
  print("-" * 80)
  print(" Location: Montgomery County, Maryland, USA")
  print(" Date Range: Last 6 months (exactly 6 months ago to today)")
  print("-" * 80)
  print(" Filtering Logic:")
  print(f"   1. Species: taxon_id IN {TARGET_TAXON_IDS}")
  print("   2. AND State = 'Maryland'")
  print("   3. AND County = 'Montgomery'")
  print("   4. AND Date within last 6 months (same day 6 months ago to today)")
  print("-" * 80)
  print(" Server: http://localhost:5000")
  print("=" * 80 + "\n")




  app.run(debug=True, port=5000, host='0.0.0.0')

