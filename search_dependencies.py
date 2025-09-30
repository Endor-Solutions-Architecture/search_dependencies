#!/usr/bin/env python3
"""
Script to search for dependencies and find which projects use them.
"""

import argparse
import json
import os
import sys
import csv
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests

# Load environment variables from .env file
load_dotenv()

# Configuration
API_URL = 'https://api.endorlabs.com/v1'

def get_env_values():
    """Get necessary values from environment variables."""
    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    initial_namespace = os.getenv("ENDOR_NAMESPACE")
    
    if not api_key or not api_secret or not initial_namespace:
        print("ERROR: API_KEY, API_SECRET, and ENDOR_NAMESPACE environment variables must be set.")
        print("Please set them in a .env file or directly in your environment.")
        sys.exit(1)
    
    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "initial_namespace": initial_namespace
    }

def get_token(api_key, api_secret):
    """Get API token using API key and secret."""
    url = f"{API_URL}/auth/api-key"
    payload = {
        "key": api_key,
        "secret": api_secret
    }
    headers = {
        "Content-Type": "application/json",
        "Request-Timeout": "60"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=600)
        response.raise_for_status()
        token = response.json().get('token')
        return token
    except requests.exceptions.RequestException as e:
        print(f"Failed to get token: {e}")
        sys.exit(1)


def parse_dependency(dependency_str):
    """Parse dependency string in format ecosystem://dependency@version."""
    try:
        if '://' not in dependency_str:
            raise ValueError("Invalid format: missing '://'")
        
        ecosystem, rest = dependency_str.split('://', 1)
        
        if '@' not in rest:
            raise ValueError("Invalid format: missing '@' for version")
        
        dependency, version = rest.rsplit('@', 1)
        
        return {
            'ecosystem': ecosystem,
            'dependency': dependency,
            'version': version,
            'full_name': f"{ecosystem}://{dependency}"
        }
    except Exception as e:
        print(f"Error parsing dependency '{dependency_str}': {e}")
        print("Expected format: ecosystem://dependency@version")
        return None

def search_dependency_usage(token, initial_namespace, dependency_info):
    """Search for projects that use a specific dependency using QUERY API."""
    print(f"\nSearching for usage of {dependency_info['full_name']}@{dependency_info['version']}...")
    
    url = f"{API_URL}/namespaces/{initial_namespace}/queries"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Request-Timeout": "600"
    }
    
    # Build query payload to join DependencyMetadata with Project data
    query_payload = {
        "meta": {
            "name": f"Dependencies with Project Info: {dependency_info['full_name']}"
        },
        "spec": {
            "query_spec": {
                "kind": "DependencyMetadata",
                "list_parameters": {
                    "filter": (
                        f"context.type==CONTEXT_TYPE_MAIN and "
                        f"spec.dependency_data.package_name=={dependency_info['full_name']} and "
                        f"spec.dependency_data.resolved_version=={dependency_info['version']}"
                    ),
                    "mask": "meta.name,spec.dependency_data,spec.importer_data",
                    "traverse": True
                },
                "references": [
                    {
                        "connect_from": "spec.importer_data.project_uuid",
                        "connect_to": "uuid",
                        "query_spec": {
                            "kind": "Project",
                            "list_parameters": {
                                "mask": "uuid,meta.name"
                            }
                        }
                    }
                ]
            }
        }
    }
    
    all_results = []
    next_page_token = None
    page_num = 1
    
    while True:
        if next_page_token:
            query_payload["spec"]["query_spec"]["list_parameters"]["page_token"] = next_page_token
        
        try:
            print(f"Fetching page {page_num}...")
            response = requests.post(url, headers=headers, json=query_payload, timeout=600)
            response.raise_for_status()
            
            data = response.json()
            
            # Extract objects from QUERY API response structure
            query_response = data.get('spec', {}).get('query_response', {})
            objects = query_response.get('list', {}).get('objects', [])
            print(f"Received {len(objects)} dependencies on page {page_num}")
            
            for obj in objects:
                dep_data = obj.get('spec', {}).get('dependency_data', {})
                importer_data = obj.get('spec', {}).get('importer_data', {})
                
                # Extract namespace from the object's tenant_meta if available
                tenant_meta = obj.get('tenant_meta') or {}
                namespace = tenant_meta.get('namespace', initial_namespace)
                
                # Extract project data from references
                project_data = {}
                meta_refs = obj.get('meta', {}).get('references', {})
                if 'Project' in meta_refs:
                    project_ref = meta_refs['Project']
                    project_objects = project_ref.get('list', {}).get('objects', [])
                    if project_objects:
                        project_obj = project_objects[0]  # Take first project
                        project_data = {
                            'project_name': project_obj.get('meta', {}).get('name', '')
                        }
                
                result = {
                    'namespace': namespace,
                    'project_uuid': importer_data.get('project_uuid', ''),
                    'project_name': project_data.get('project_name', ''),
                    'dependency_name': dep_data.get('package_name', ''),
                    'dependency_version': dep_data.get('resolved_version', ''),
                    'dependency_scope': dep_data.get('scope', ''),
                    'parent_package_version_name': importer_data.get('package_version_name', '')
                }
                all_results.append(result)
                print(f"  Found usage of {result['dependency_name']}@{result['dependency_version']} in project: {result['project_name']} ({result['project_uuid']}) in namespace: {namespace}")
                if result['parent_package_version_name']:
                    print(f"    └── Parent package version: {result['parent_package_version_name']}")
            
            # For QUERY API, pagination is in spec.query_response.list.response
            query_response = data.get('spec', {}).get('query_response', {})
            next_page_token = query_response.get('list', {}).get('response', {}).get('next_page_token')
            if not next_page_token:
                break
            
            page_num += 1
                
        except requests.exceptions.RequestException as e:
            print(f"Failed to search dependencies: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            break
    
    return all_results

def save_results_json(results, filename):
    """Save results to JSON file."""
    try:
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to JSON: {filename}")
    except Exception as e:
        print(f"Error saving JSON file: {e}")

def save_results_csv(results, filename):
    """Save results to CSV file."""
    if not results:
        print("No results to save to CSV")
        return
    
    try:
        with open(filename, 'w', newline='') as f:
            # Get all unique keys from all results
            fieldnames = set()
            for result in results:
                fieldnames.update(result.keys())
            fieldnames = sorted(list(fieldnames))
            
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for result in results:
                writer.writerow(result)
        
        print(f"Results saved to CSV: {filename}")
    except Exception as e:
        print(f"Error saving CSV file: {e}")

def display_results(results, dependency_info):
    """Display results on terminal."""
    print(f"\n{'='*60}")
    print(f"SEARCH RESULTS for {dependency_info['full_name']}@{dependency_info['version']}")
    print(f"{'='*60}")
    
    if not results:
        print("No projects found using this dependency.")
        return
    
    print(f"Found {len(results)} usage(s) across {len(set(r['namespace'] for r in results))} namespace(s)")
    print()
    
    # Group by namespace and project
    grouped = {}
    for result in results:
        namespace = result['namespace']
        project_name = result['project_name'] or 'Unknown Project'
        project_key = f"{project_name} ({result['project_uuid']})"
        
        if namespace not in grouped:
            grouped[namespace] = {}
        if project_key not in grouped[namespace]:
            grouped[namespace][project_key] = []
        
        grouped[namespace][project_key].append(result)
    
    for namespace, projects in grouped.items():
        print(f"Namespace: {namespace}")
        for project_key, usages in projects.items():
            print(f"  └── Project: {project_key}")
            for usage in usages:
                print(f"      ├── Scope: {usage['dependency_scope']}")
                if usage['parent_package_version_name']:
                    print(f"      └── Parent package version: {usage['parent_package_version_name']}")
                else:
                    print(f"      └── (No parent package version info)")
        print()

def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Search for dependencies and find which projects use them.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python search_dependencies.py --dependencies "npm://lodash@4.17.21"
  python search_dependencies.py --dependencies "npm://react@18.2.0,maven://org.springframework:spring-core@5.3.21"
        """
    )
    parser.add_argument(
        '--dependencies', 
        type=str, 
        required=True, 
        help='Comma-separated list of dependencies in format: ecosystem://dependency@version'
    )
    
    args = parser.parse_args()
    
    # Parse dependencies
    dependency_strings = [dep.strip() for dep in args.dependencies.split(',')]
    dependencies = []
    
    for dep_str in dependency_strings:
        dep_info = parse_dependency(dep_str)
        if dep_info:
            dependencies.append(dep_info)
        else:
            print(f"Skipping invalid dependency: {dep_str}")
    
    if not dependencies:
        print("ERROR: No valid dependencies provided.")
        sys.exit(1)
    
    # Get environment values
    env = get_env_values()
    
    # Get API token
    token = get_token(env["api_key"], env["api_secret"])
    if not token:
        print("Failed to get API token.")
        sys.exit(1)
    
    # Search for each dependency
    all_results = {}
    
    for dep_info in dependencies:
        results = search_dependency_usage(token, env["initial_namespace"], dep_info)
        all_results[f"{dep_info['full_name']}@{dep_info['version']}"] = results
        
        # Display results for this dependency
        display_results(results, dep_info)
    
    # Generate output filenames
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_filename = f"dependency_search_results_{timestamp}.json"
    csv_filename = f"dependency_search_results_{timestamp}.csv"
    
    # Flatten results for CSV output
    flat_results = []
    for dep_name, results in all_results.items():
        for result in results:
            result['searched_dependency'] = dep_name
            flat_results.append(result)
    
    # Save results
    save_results_json(all_results, json_filename)
    save_results_csv(flat_results, csv_filename)
    
    # Summary
    total_usages = sum(len(results) for results in all_results.values())
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Dependencies searched: {len(dependencies)}")
    print(f"Total usages found: {total_usages}")
    print(f"Results saved to: {json_filename}, {csv_filename}")

if __name__ == "__main__":
    main()
